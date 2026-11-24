"""Expansão oficial de geração (ANEEL RALIE) como eventos de cenário sobre um caso base.

O RALIE (Relatório de Acompanhamento da Expansão da Oferta de Geração) lista as
usinas outorgadas que ainda não estão (ou estão só em parte) em operação
comercial, com o ponto de conexão (subestação e tensão) e, por unidade geradora,
a data prevista de operação comercial.

Este módulo não acessa a rede: recebe as linhas dos dois CSV (usina e unidade
geradora) e as tabelas do caso base, e devolve eventos e pendências. O download
fica em ``scripts/expansao_ralie.py``.

Casamento com o deck: o nome da subestação de conexão (``NomConexao``) é
comparado com os códigos de seis caracteres das subestações do deck
(``J.CAM3``, ``ACU3``, ``CEAMIR``), só na mesma UF e na tensão de conexão.
O código do deck costuma ser uma abreviação do nome: o score mede quanto do
código aparece, em ordem, nas letras do nome, se a primeira letra bate e se o
número (I, II, III → 1, 2, 3) é o mesmo.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

ROMANOS = {
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
    "IX": "9",
    "X": "10",
    "XI": "11",
    "XII": "12",
    "XIII": "13",
    "XIV": "14",
    "XV": "15",
}
# palavras que não ajudam a identificar a subestação
VAZIAS = {
    "SE",
    "SUBESTACAO",
    "SUBESTACOES",
    "USINA",
    "UHE",
    "UTE",
    "PCH",
    "EOL",
    "UFV",
    "LT",
    "KV",
    "DE",
    "DA",
    "DO",
    "DAS",
    "DOS",
    "E",
    "C1",
    "C2",
    "SECCIONADORA",
    "COLETORA",
    "ELEVADORA",
    "ANTIGA",
}
# abreviações comuns nos nomes por extenso (o código do deck usa a forma curta)
ABREV = {
    "SAO": "S",
    "SANTO": "STO",
    "SANTA": "STA",
    "NOSSA": "N",
    "SENHORA": "SRA",
    "PRESIDENTE": "PRES",
    "GOVERNADOR": "GOV",
    "DOUTOR": "DR",
    "ENGENHEIRO": "ENG",
    "CORONEL": "CEL",
    "MONSENHOR": "MONS",
}
SEM_CONEXAO = {"", "OUTRO", "OUTROS", "NAO DEFINIDA NA OUTORGA", "NAO DEFINIDA NA OUTORGAA"}

FONTE_RALIE = {"UFV": "UFV", "EOL": "EOL", "PCH": "PCH", "UHE": "UHE", "CGH": "CGH", "UTN": "UTE"}
LIMIAR = 0.72  # score mínimo para casar sem revisão
MARGEM = 0.04  # diferença mínima para a 2ª subestação candidata (se for outra)
LIMIAR_DUP = 0.85  # nome parecido com usina do deck: possivelmente já no deck


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(nome: str) -> list[str]:
    """Nome normalizado em palavras: maiúsculas, sem acento nem pontuação, romanos em arábicos."""
    s = sem_acento(nome or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    out = []
    for t in s.split():
        if t in VAZIAS:
            continue
        t = ROMANOS.get(t, t)
        out.append(ABREV.get(t, t))
    return out


def normalizar(nome: str) -> str:
    return " ".join(tokens(nome))


def _subseq(curto: str, longo: str) -> float:
    """Fração de `curto` encontrada em ordem dentro de `longo` (greedy)."""
    if not curto:
        return 0.0
    j = 0
    for c in longo:
        if j < len(curto) and c == curto[j]:
            j += 1
    return j / len(curto)


CONECTIVOS = {"DE", "DA", "DO", "DAS", "DOS", "E"}
ROMANO_DE = {v: k for k, v in ROMANOS.items()}


def _formas(tk: list[str]) -> list[str]:
    """Formas abreviadas das letras do nome: iniciais das k primeiras palavras + o resto por extenso."""
    pal = [t for t in tk if not t.isdigit()]
    return ["".join(p[0] for p in pal[:k]) + "".join(pal[k:]) for k in range(len(pal) + 1)]


def score_codigo(nome: str, codigo: str) -> float:
    """Quanto o código de subestação do deck parece uma abreviação do nome (0 a 1)."""
    tk = tokens(nome)
    if not tk:
        return 0.0
    cod = re.sub(r"[^A-Z0-9]", "", sem_acento(codigo or "").upper())
    if not cod:
        return 0.0
    num_nome = next((t for t in reversed(tk) if t.isdigit()), None)
    # com os conectivos (Morro do Chapéu -> MDCHAP) e sem eles (MCHAP)
    tk_com = [
        ROMANOS.get(t, t)
        for t in re.sub(r"[^A-Z0-9]+", " ", sem_acento(nome).upper()).split()
        if t not in VAZIAS - CONECTIVOS
    ]
    letras_nome = [
        x for x in {"".join(t for t in tk if not t.isdigit()), "".join(t for t in tk_com if not t.isdigit())} if x
    ]
    num_cod = "".join(c for c in cod if c.isdigit()) or None
    letras_cod = "".join(c for c in cod if c.isalpha())
    if num_nome and not num_cod and num_nome in ROMANO_DE:
        rom = ROMANO_DE[num_nome]
        if letras_cod.endswith(rom) and len(letras_cod) > len(rom) + 2:
            letras_cod, num_cod = letras_cod[: -len(rom)], num_nome
    if not letras_cod or not letras_nome or all(letras_cod[0] != ln[0] for ln in letras_nome):
        return 0.0
    if num_nome and num_cod and num_nome != num_cod:
        return 0.0
    sub = max(_subseq(letras_cod, ln) for ln in letras_nome)
    formas = _formas(tk) + _formas(tk_com)
    parecido = max(difflib.SequenceMatcher(None, letras_cod, f[: len(letras_cod) + 1]).ratio() for f in formas if f)
    s = 0.6 * sub + 0.4 * parecido
    if letras_cod in letras_nome or letras_cod == tk[0]:
        s = max(s, 0.95)
    if num_nome and num_cod is None:
        s -= 0.08
    elif num_cod and num_nome is None:
        s -= 0.2
    return max(0.0, min(1.0, s))


GENERICAS = {"CJ", "CJT", "COMPLEXO", "CONJUNTO", "EOLICO", "EOLICA", "SOLAR", "FOTOVOLTAICA", "FOTOVOLTAICO", "PARQUE"}


def parecidos(a: str, b: str) -> float:
    """Semelhança de dois nomes de usina (0 a 1), tolerante a 'CJ' e à ordem; número diferente = 0."""
    ta = [t for t in tokens(a) if t not in GENERICAS]
    tb = [t for t in tokens(b) if t not in GENERICAS]
    if not ta or not tb:
        return 0.0
    na = {t for t in ta if t.isdigit()}
    nb = {t for t in tb if t.isdigit()}
    if na and nb and not (na & nb):
        return 0.0
    sa, sb = " ".join(ta), " ".join(tb)
    r = difflib.SequenceMatcher(None, sa, sb).ratio()
    pa, pb = {t for t in ta if not t.isdigit()}, {t for t in tb if not t.isdigit()}
    menor = min(pa, pb, key=len) if pa and pb else set()
    if pa and pb and (pa == pb or ((len(menor) >= 2 or len(next(iter(menor))) >= 6) and (pa <= pb or pb <= pa))):
        r = max(r, 0.9)
    return r


def nome_usina_deck(nome: str) -> str:
    """'CNLBI  _CJ LAGOA DO BARRO I_05040_UEE' -> 'CJ LAGOA DO BARRO I'."""
    p = (nome or "").split("_")
    return p[1] if len(p) >= 3 else nome


def _f(v: str | None) -> float:
    v = (v or "").strip().replace(".", "").replace(",", ".") if v and "," in v else (v or "").strip()
    try:
        return float(v)
    except ValueError:
        return 0.0


def _data(v: str | None) -> date | None:
    try:
        return date.fromisoformat((v or "").strip()[:10])
    except ValueError:
        return None


def fonte_energia(u: dict) -> str:
    sig = (u.get("SigTipoGeracao") or "").strip()
    if sig == "UTE":
        return "BIO" if sem_acento(u.get("DscOrigemCombustivel") or "").lower().startswith("biomassa") else "UTE"
    return FONTE_RALIE.get(sig, sig)


def confianca(u: dict) -> str:
    """'contratado' quando a obra está em andamento (DscSituacaoObra); senão 'previsto'."""
    return "contratado" if sem_acento(u.get("DscSituacaoObra") or "").lower().startswith("em andamento") else "previsto"


def _mes(d: date) -> date:
    return date(d.year, d.month, 1)


def proximo_mes(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


@dataclass
class Subestacao:
    nome: str
    uf: str
    barras: list[dict] = field(default_factory=list)

    def kvs(self) -> set[float]:
        return {b["kv"] for b in self.barras}


@dataclass
class Casamento:
    subestacao: str | None
    barra_id: str | None
    score: float
    candidatos: list[tuple[str, float]]
    via: str  # conexao, complexo, seccionamento, nenhum
    motivo: str = ""


def indice_subestacoes(barras: Iterable[dict]) -> dict[str, dict[str, Subestacao]]:
    """UF -> subestação -> barras (só barras com UF)."""
    out: dict[str, dict[str, Subestacao]] = defaultdict(dict)
    for b in barras:
        uf = b.get("uf")
        if not uf:
            continue
        s = out[uf].setdefault(b["subestacao"], Subestacao(b["subestacao"], uf))
        s.barras.append(b)
    return out


def _kv_ok(kv: float, alvo: float | None) -> bool:
    return alvo is None or abs(kv - alvo) <= 0.06 * alvo


def escolher_barra(s: Subestacao, kv: float | None) -> dict | None:
    """Barra de maior kV igual à tensão de conexão; prefere a barra principal (nome com UF e kV)."""
    cand = [b for b in s.barras if _kv_ok(b["kv"], kv)] or ([] if kv else list(s.barras))
    if not cand:
        return None
    alvo = max(b["kv"] for b in cand)
    cand = [b for b in cand if b["kv"] == alvo]

    def principal(b: dict) -> int:
        n = b.get("nome") or ""
        return 0 if re.search(rf"{s.uf}\d{{3}}$", n) and not n.endswith("000") else 1

    return min(cand, key=lambda b: (principal(b), b.get("num_pwf") or 0))


def casar_nome(nome: str, uf: str, kv: float | None, idx: dict[str, dict[str, Subestacao]]) -> Casamento:
    subs = idx.get(uf, {})
    pontos = []
    for s in subs.values():
        if kv is not None and not any(_kv_ok(k, kv) for k in s.kvs()):
            continue
        sc = score_codigo(nome, s.nome)
        if sc > 0:
            pontos.append((sc, s.nome))
    pontos.sort(key=lambda x: (-x[0], x[1]))
    top = [(n, round(sc, 3)) for sc, n in pontos[:3]]
    if not pontos:
        return Casamento(None, None, 0.0, top, "nenhum", "nenhuma subestação parecida na UF e na tensão")
    sc, melhor = pontos[0]
    if sc < LIMIAR:
        return Casamento(None, None, sc, top, "nenhum", f"score {sc:.2f} abaixo de {LIMIAR}")
    num = next((t for t in reversed(tokens(nome)) if t.isdigit()), None)

    def com_numero(cod: str) -> bool:
        return bool(num) and "".join(c for c in cod if c.isdigit()) == num

    empate = len(pontos) > 1 and pontos[1][0] > sc - MARGEM
    if empate and com_numero(melhor) and not com_numero(pontos[1][1]):
        empate = False
    if empate:
        return Casamento(None, None, sc, top, "nenhum", "duas subestações com score próximo")
    b = escolher_barra(subs[melhor], kv)
    if b is None:
        return Casamento(None, None, sc, top, "nenhum", "subestação sem barra na tensão")
    return Casamento(melhor, b["barra_id"], sc, top, "conexao")


def casar_usina(u: dict, idx: dict[str, dict[str, Subestacao]]) -> Casamento:
    nome = (u.get("NomConexao") or "").strip()
    uf = (u.get("SigUFPrincipal") or "").strip()
    kv = _f(u.get("MdaTensaoConexao")) or None
    if normalizar(nome) in {normalizar(x) for x in SEM_CONEXAO} or not tokens(nome):
        return Casamento(None, None, 0.0, [], "nenhum", "sem ponto de conexão definido na outorga")
    if (u.get("DscTipoConexao") or "").strip() == "Seccionamento":
        # "A - B": a usina secciona a LT entre A e B; liga na ponta que casar melhor (aproximação)
        partes = [p for p in re.split(r"\s+-\s+|/|–", nome) if tokens(p)]
        melhores = [casar_nome(p, uf, kv, idx) for p in partes]
        ok = [c for c in melhores if c.barra_id]
        if ok:
            c = max(ok, key=lambda c: c.score)
            c.via = "seccionamento"
            return c
        cand = sorted({x for c in melhores for x in c.candidatos}, key=lambda x: -x[1])[:3]
        return Casamento(
            None, None, max((c.score for c in melhores), default=0.0), cand, "nenhum", "seccionamento sem ponta casada"
        )
    return casar_nome(nome, uf, kv, idx)


def montar(
    usinas_ralie: list[dict],
    unidades_ralie: list[dict],
    base: dict[str, list[dict]],
    data_base: date,
    rotulo_fonte: str,
    horizonte: date = date(2031, 12, 31),
) -> dict[str, Any]:
    """Eventos 'entra'/'altera' das usinas do RALIE sobre o caso base.

    Devolve {"eventos": [...], "pendencias": [...], "no_deck": [...], "casadas": [...]}.
    Data de cada unidade: DatPrevisaoOpComercialSFG, senão DatUGInicioOpComerOutorgado;
    datas já passadas (atrasadas) vão para o mês seguinte à data do caso base.
    """
    barras = base["barras"]
    idx = indice_subestacoes(barras)
    barra_por_id = {b["barra_id"]: b for b in barras}
    sub_de_barra = {b["barra_id"]: (b.get("uf"), b["subestacao"]) for b in barras}
    deck_por_sub: dict[tuple, list[dict]] = defaultdict(list)
    deck_por_uf_fonte: dict[tuple, list[dict]] = defaultdict(list)
    for us in base["usinas"]:
        k = sub_de_barra.get(us["barra_id"])
        if k:
            deck_por_sub[k].append(us)
            deck_por_uf_fonte[(k[0], us["fonte_energia"])].append(us)

    unidades: dict[str, list[dict]] = defaultdict(list)
    for g in unidades_ralie:
        unidades[g["CodCEG"]].append(g)
    primeiro = proximo_mes(data_base)

    # complexos: conexão casada de uma usina vale para as do mesmo complexo sem conexão
    casamentos: dict[str, Casamento] = {}
    sin = [u for u in usinas_ralie if "interligado" in sem_acento(u.get("DscSistema") or "").lower()]
    for u in sin:
        casamentos[u["CodCEG"]] = casar_usina(u, idx)
    por_complexo: dict[str, Casamento] = {}
    for u in sin:
        c = casamentos[u["CodCEG"]]
        cx = (u.get("NomComplexo") or "").strip()
        if cx and c.barra_id and (cx not in por_complexo or c.score > por_complexo[cx].score):
            por_complexo[cx] = c
    for u in sin:
        c = casamentos[u["CodCEG"]]
        cx = (u.get("NomComplexo") or "").strip()
        if not c.barra_id and cx in por_complexo and c.motivo.startswith("sem ponto"):
            o = por_complexo[cx]
            casamentos[u["CodCEG"]] = Casamento(
                o.subestacao, o.barra_id, round(o.score * 0.9, 3), o.candidatos, "complexo"
            )

    eventos, pendencias, no_deck, casadas = [], [], [], []
    for u in sin:
        cod = u["CodCEG"]
        fonte = fonte_energia(u)
        conf = confianca(u)
        # potência por mês (só unidades até o horizonte)
        por_mes: dict[date, float] = defaultdict(float)
        atrasadas = 0
        for g in unidades.get(cod, []):
            d = _data(g.get("DatPrevisaoOpComercialSFG")) or _data(g.get("DatUGInicioOpComerOutorgado"))
            if d is None:
                continue
            if d < primeiro:
                atrasadas += 1
                d = primeiro
            if d > horizonte:
                continue
            por_mes[_mes(d)] += _f(g.get("MdaPotenciaUnitaria")) / 1000.0
        mw_total = sum(por_mes.values())
        if mw_total <= 0:
            continue
        c = casamentos[cod]
        linha = {
            "CodCEG": cod,
            "nome": u.get("NomEmpreendimento"),
            "complexo": u.get("NomComplexo"),
            "fonte": fonte,
            "uf": u.get("SigUFPrincipal"),
            "conexao": u.get("NomConexao"),
            "tensao_kv": _f(u.get("MdaTensaoConexao")) or None,
            "mw_ate_horizonte": round(mw_total, 1),
            "primeira_data": min(por_mes).isoformat(),
            "situacao_obra": u.get("DscSituacaoObra"),
            "score": round(c.score, 3),
            "via": c.via,
            "candidatos": c.candidatos,
            "motivo": c.motivo,
        }
        if not c.barra_id:
            pendencias.append(linha)
            continue
        uf_sub = sub_de_barra[c.barra_id]
        # já no deck? mesma fonte na mesma subestação (ou, para UHE/UTE/PCH, na UF) com nome parecido
        vizinhas = list(deck_por_sub.get(uf_sub, []))
        if fonte in ("UHE", "UTE", "PCH", "CGH", "BIO"):
            vizinhas += deck_por_uf_fonte.get((uf_sub[0], fonte), [])
        nomes = [u.get("NomEmpreendimento") or "", u.get("NomComplexo") or ""]
        dup = max(
            (
                (parecidos(n, nome_usina_deck(v["nome"])), v)
                for v in vizinhas
                if v["fonte_energia"] == fonte
                for n in nomes
                if n
            ),
            key=lambda x: x[0],
            default=(0.0, None),
        )
        if dup[0] >= LIMIAR_DUP:
            no_deck.append(
                {**linha, "usina_deck": dup[1]["usina_id"], "nome_deck": dup[1]["nome"], "semelhanca": round(dup[0], 3)}
            )
            continue
        b = barra_por_id[c.barra_id]
        uid = f"{fonte}-A{u.get('IdeNucleoCEG') or cod}"
        nota = (
            f"RALIE {cod}; conexão '{u.get('NomConexao')}' {u.get('MdaTensaoConexao')} kV -> {c.subestacao} "
            f"({c.via}, score {c.score:.2f}); obra: {u.get('DscSituacaoObra')}; viabilidade: {u.get('DscViabilidade')}"
            + (f"; {atrasadas} UG com data já passada levadas a {primeiro.isoformat()}" if atrasadas else "")
        )
        acumulado = 0.0
        for k, mes in enumerate(sorted(por_mes)):
            acumulado += por_mes[mes]
            if k == 0:
                eventos.append(
                    {
                        "evento_id": f"ralie-{cod}-entra",
                        "data": mes.isoformat(),
                        "acao": "entra",
                        "entidade": "usinas",
                        "entidade_id": uid,
                        "registro": {
                            "usina_id": uid,
                            "cod_deck": None,
                            "nome": u.get("NomEmpreendimento"),
                            "fonte_energia": fonte,
                            "barra_id": c.barra_id,
                            "submercado": b.get("submercado"),
                            "cap_mw": round(acumulado, 3),
                            "flag_corte": 1 if fonte in ("EOL", "UFV") else 0,
                            "ids_ons": [cod],
                            "ponto_conexao_ons": u.get("NomConexao"),
                            "grupo_eixo": u.get("NomComplexo") or None,
                        },
                        "fonte": rotulo_fonte,
                        "confianca": conf,
                        "nota": nota,
                    }
                )
            else:
                eventos.append(
                    {
                        "evento_id": f"ralie-{cod}-{mes.strftime('%Y%m')}",
                        "data": mes.isoformat(),
                        "acao": "altera",
                        "entidade": "usinas",
                        "entidade_id": uid,
                        "campos": {"cap_mw": round(acumulado, 3)},
                        "fonte": rotulo_fonte,
                        "confianca": conf,
                        "nota": f"RALIE {cod}: potência acumulada das UG previstas até {mes.strftime('%m/%Y')}",
                    }
                )
        casadas.append(
            {
                **linha,
                "usina_id": uid,
                "barra_id": c.barra_id,
                "subestacao": c.subestacao,
                "submercado": b.get("submercado"),
            }
        )
    return {"eventos": eventos, "pendencias": pendencias, "no_deck": no_deck, "casadas": casadas}
