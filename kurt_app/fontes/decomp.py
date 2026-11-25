"""Leitura dos insumos básicos de um deck DECOMP (pasta ou ZIP), sem pandas.

O que sai de cada arquivo:

- ``dadger.rvN``:
  - DT: data do início do estudo;
  - SB: códigos dos submercados;
  - DP: carga em MWmed e horas por patamar, que também dão a duração dos estágios;
  - CT: térmicas, com inflexibilidade, disponibilidade e CVU por patamar;
  - PQ: pequenas usinas (nos decks atuais, só a MMGD, por fonte e submercado);
  - RI: Itaipu, de onde sai a carga da ANDE por patamar;
  - UH: volume inicial em % do volume útil.
- ``renovaveis.csv``: geração das usinas não despachadas em parques equivalentes por
  região e fonte (cartões PEE-*), por estágio e patamar.
- ``vazoes.rvN``: binário do GEVAZP. Traz as incrementais semanais previstas por posto
  (determinísticas) e os cenários de incrementais do mês seguinte, com probabilidade.
  O layout está em ``pulsar/docs/layout-VAZOES.RVx.md``.
- ``cortdeco.*`` e ``mapcut.*``: só existem quando o deck vem de uma rodada executada
  (a FCF própria do DECOMP). Este módulo só informa os caminhos.

As colunas seguem as máscaras de comentário do próprio dadger (posições 0-based).
"""

from __future__ import annotations

import json
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

PATAMARES = 3
NOMES_PATAMAR = ("pesada", "media", "leve")
NPOS_PADRAO = 320  # postos no VAZOES.RVx de versões antigas do GEVAZP, que não gravam NPOS

# fonte pelo sufixo do nome do parque equivalente (PEE) e da pequena usina (PQ)
FONTES_PQ = {"PCH": "PCH", "PCT": "BIO", "EOL": "EOL", "UFV": "UFV"}
# no PQ, o prefixo é a região da MMGD
REGIOES_PQ = {"SECO": "SE", "SUL": "S", "NE": "NE", "N": "N"}


class DeckErro(ValueError):
    pass


@dataclass
class DeckDecomp:
    origem: str
    revisao: str  # rv0, rv1...
    titulo: str
    inicio: date
    estagios: pa.Table
    carga: pa.Table
    termicas: pa.Table
    pequenas: pa.Table
    ande: pa.Table
    renovaveis: pa.Table
    volumes: pa.Table
    vazoes: pa.Table
    vazoes_cenarios: pa.Table
    fcf: dict[str, str] | None
    avisos: list[str] = field(default_factory=list)

    def tabelas(self) -> dict[str, pa.Table]:
        return {
            "estagios": self.estagios,
            "carga": self.carga,
            "termicas": self.termicas,
            "pequenas": self.pequenas,
            "ande": self.ande,
            "renovaveis": self.renovaveis,
            "volumes": self.volumes,
            "vazoes": self.vazoes,
            "vazoes_cenarios": self.vazoes_cenarios,
        }


# ---------------------------------------------------------------- acesso aos arquivos


class _Arquivos:
    """Arquivos do deck pelo nome em minúsculas, de uma pasta ou de um ZIP."""

    def __init__(self, origem: Path):
        self.origem = origem
        self.nomes: dict[str, str] = {}
        if origem.is_dir():
            for p in origem.iterdir():
                if p.is_file():
                    self.nomes[p.name.lower()] = str(p)
            self._zip = None
        elif zipfile.is_zipfile(origem):
            self._zip = zipfile.ZipFile(origem)
            for n in self._zip.namelist():
                base = n.rsplit("/", 1)[-1]
                if base:
                    self.nomes[base.lower()] = n
        else:
            raise DeckErro(f"{origem}: não é pasta nem ZIP")

    def ler(self, nome: str) -> bytes | None:
        n = self.nomes.get(nome.lower())
        if n is None:
            return None
        if self._zip is not None:
            return self._zip.read(n)
        return Path(n).read_bytes()

    def com_prefixo(self, prefixo: str) -> list[str]:
        return sorted(k for k in self.nomes if k.startswith(prefixo))

    def caminho(self, nome: str) -> str:
        n = self.nomes[nome.lower()]
        return n if self._zip is None else f"{self.origem}!{n}"


def _linhas(b: bytes) -> list[str]:
    return b.decode("latin-1").replace("\r", "").split("\n")


def _f(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s: str) -> int | None:
    v = _f(s)
    return None if v is None else int(v)


# ---------------------------------------------------------------- dadger


def _registros(linhas: list[str], mnem: str) -> list[str]:
    tam = len(mnem)
    return [ln for ln in linhas if ln[:tam] == mnem and (len(ln) == tam or ln[tam] == " ")]


def _submercados(linhas: list[str]) -> dict[int, str]:
    out = {}
    for ln in _registros(linhas, "SB"):
        c, m = _i(ln[4:6]), ln[9:11].strip()
        if c is not None and m:
            out[c] = m
    return out


def _data_inicio(linhas: list[str]) -> date:
    dt = _registros(linhas, "DT")
    if not dt:
        raise DeckErro("dadger sem registro DT (data do estudo)")
    p = dt[0].split()
    return date(int(p[3]), int(p[2]), int(p[1]))


def _carga(linhas: list[str], subs: dict[int, str]) -> tuple[list[dict], dict[int, list[float]]]:
    """DP: (estágio, submercado, patamar, horas, mwmed); devolve também as horas por estágio."""
    out, horas = [], {}
    for ln in _registros(linhas, "DP"):
        est, sub, npat = _i(ln[4:6]), _i(ln[9:11]), _i(ln[14:15]) or PATAMARES
        if est is None or sub is None:
            continue
        hs = []
        for p in range(npat):
            a = 19 + 20 * p
            mw, h = _f(ln[a : a + 10]), _f(ln[a + 10 : a + 20])
            hs.append(h or 0.0)
            if mw is not None:
                out.append(
                    {
                        "estagio": est,
                        "submercado": subs.get(sub, str(sub)),
                        "patamar": p + 1,
                        "horas": h,
                        "mwmed": mw,
                    }
                )
        if any(hs):
            horas.setdefault(est, hs)
    return out, horas


def _termicas(linhas: list[str], subs: dict[int, str]) -> list[dict]:
    out = []
    for ln in _registros(linhas, "CT"):
        cod, sub, est = _i(ln[4:7]), _i(ln[9:11]), _i(ln[24:26])
        if cod is None or est is None:
            continue
        nome = ln[14:24].strip()
        for p in range(PATAMARES):
            a = 29 + 20 * p
            inflex, disp, cvu = _f(ln[a : a + 5]), _f(ln[a + 5 : a + 10]), _f(ln[a + 10 : a + 20])
            if inflex is None and disp is None and cvu is None:
                continue
            out.append(
                {
                    "codigo": cod,
                    "nome": nome,
                    "submercado": subs.get(sub, str(sub)) if sub is not None else None,
                    "estagio": est,
                    "patamar": p + 1,
                    "inflex_mw": inflex,
                    "disp_mw": disp,
                    "cvu": cvu,
                }
            )
    return out


def _pequenas(linhas: list[str], subs: dict[int, str]) -> list[dict]:
    """PQ: nome em [4:14], submercado em [15], estágio em [19:21], valor por patamar em [24:39]."""
    out = []
    for ln in _registros(linhas, "PQ"):
        nome, sub, est = ln[4:14].strip(), _i(ln[14:16]), _i(ln[19:21])
        if not nome or est is None:
            continue
        m = re.match(r"^([A-Z]+)_([A-Z]+)(gd)?$", nome)
        fonte = FONTES_PQ.get(m.group(2), m.group(2)) if m else None
        mmgd = bool(m and m.group(3))
        for p in range(PATAMARES):
            a = 24 + 5 * p
            v = _f(ln[a : a + 5])
            if v is None:
                continue
            out.append(
                {
                    "nome": nome,
                    "submercado": subs.get(sub, str(sub)) if sub is not None else None,
                    "fonte": fonte,
                    "mmgd": mmgd,
                    "estagio": est,
                    "patamar": p + 1,
                    "mwmed": v,
                }
            )
    return out


def _ande(linhas: list[str]) -> list[dict]:
    """RI: por patamar, MIN60 MAX60 MIN50 MAX50 ANDE em campos de 7 colunas a partir de [16]."""
    out = []
    for ln in _registros(linhas, "RI"):
        est = _i(ln[9:11])
        if est is None:
            continue
        for p in range(PATAMARES):
            a = 16 + 35 * p + 28
            v = _f(ln[a : a + 7])
            if v is not None:
                out.append({"estagio": est, "patamar": p + 1, "mwmed": v})
    return out


def _volumes(linhas: list[str]) -> list[dict]:
    out = []
    for ln in _registros(linhas, "UH"):
        u, ree = _i(ln[4:7]), _i(ln[9:11])
        if u is None:
            continue
        out.append({"usina": u, "ree": ree, "vol_ini_pct": _f(ln[14:24])})
    return out


def _estagios(inicio: date, horas: dict[int, list[float]]) -> list[dict]:
    out, d = [], inicio
    for est in sorted(horas):
        hs = horas[est]
        total = sum(hs)
        fim = d + timedelta(hours=total)
        out.append(
            {
                "estagio": est,
                "inicio": d,
                "fim": fim - timedelta(days=1) if total else d,
                "dias": round(total / 24, 4),
                **{f"horas_{NOMES_PATAMAR[p]}": (hs[p] if p < len(hs) else None) for p in range(PATAMARES)},
            }
        )
        d = fim
    return out


# ---------------------------------------------------------------- renovaveis.csv


def _renovaveis(b: bytes | None, subs: dict[int, str]) -> list[dict]:
    if not b:
        return []
    nome, sub, pot, ger = {}, {}, {}, []
    for ln in _linhas(b):
        if not ln or ln.startswith("&"):
            continue
        c = [x.strip() for x in ln.split(";")]
        k = c[0]
        try:
            if k == "PEE-CAD":
                nome[int(c[1])] = c[2]
            elif k == "PEE-SUBM":
                sub[int(c[1])] = int(c[2])
            elif k == "PEE-POT-INST-PER":
                for e in range(int(c[2]), int(c[3]) + 1):
                    pot[(int(c[1]), e)] = float(c[4])
            elif k == "PEE-GER-PER-PAT":
                for e in range(int(c[2]), int(c[3]) + 1):
                    ger.append((int(c[1]), e, int(c[4]), float(c[5])))
        except (IndexError, ValueError):
            continue
    out = []
    for i, e, p, mw in ger:
        n = nome.get(i, str(i))
        suf = n.rsplit("_", 1)[-1]
        cap = pot.get((i, e))
        out.append(
            {
                "equivalente": n,
                "submercado": subs.get(sub.get(i, -1), str(sub.get(i))) if i in sub else None,
                "fonte": FONTES_PQ.get(suf, suf),
                "estagio": e,
                "patamar": p,
                "mwmed": mw,
                # 99999 = sem limite declarado
                "capacidade_mw": cap if cap is not None and cap < 99_999 else None,
            }
        )
    return out


# ---------------------------------------------------------------- vazoes.rvN


def ler_vazoes(b: bytes) -> tuple[list[dict], list[dict], dict]:
    """Incrementais semanais previstas (bloco 5) e cenários mensais (bloco 6) do VAZOES.RVx."""
    cab = np.frombuffer(b[: NPOS_PADRAO * 4], dtype="<i4")
    nest = int(cab[1])
    if not 1 <= nest <= 20:
        raise DeckErro(f"vazoes: número de estágios inválido ({nest})")
    aberturas = [int(x) for x in cab[2 : 2 + nest]]
    npos = int(cab[2 + nest]) or NPOS_PADRAO
    if npos <= 0 or npos > 1000:
        npos = NPOS_PADRAO
    rec = npos * 4
    nreg = len(b) // rec
    regs = np.frombuffer(b[: nreg * rec], dtype="<i4").reshape(nreg, npos)
    r3 = regs[2]
    nnos, prod = 0, 1
    for a in aberturas:
        prod *= a
        nnos += prod
    nregp = math.ceil(nnos / npos)
    probs = np.frombuffer(b[3 * rec : (3 + nregp) * rec], dtype="<f4")[:nnos]
    ini5 = 3 + nregp
    nsem = nest - 1
    ncen = aberturas[-1]
    sem = []
    for k in range(nsem):
        v = regs[ini5 + k]
        for col in np.nonzero(v)[0]:
            sem.append({"posto": int(col) + 1, "estagio": k + 1, "m3s": float(v[col])})
    cen = []
    pfolhas = probs[nsem : nsem + ncen]
    for c in range(ncen):
        v = regs[ini5 + nsem + c]
        for col in np.nonzero(v)[0]:
            cen.append(
                {
                    "posto": int(col) + 1,
                    "estagio": nest,
                    "cenario": c + 1,
                    "probabilidade": float(pfolhas[c]) if c < len(pfolhas) else None,
                    "m3s": float(v[col]),
                }
            )
    meta = {
        "estagios": nest,
        "aberturas": aberturas,
        "postos": npos,
        "semanas_previstas": int(r3[0]),
        "mes_inicial": int(r3[2]),
        "ano_inicial": int(r3[3]),
        "cenarios": ncen,
    }
    return sem, cen, meta


# ---------------------------------------------------------------- deck inteiro


ESQUEMAS = {
    "estagios": pa.schema(
        [
            ("estagio", pa.int32()),
            ("inicio", pa.date32()),
            ("fim", pa.date32()),
            ("dias", pa.float64()),
            ("horas_pesada", pa.float64()),
            ("horas_media", pa.float64()),
            ("horas_leve", pa.float64()),
        ]
    ),
    "carga": pa.schema(
        [
            ("estagio", pa.int32()),
            ("submercado", pa.string()),
            ("patamar", pa.int32()),
            ("horas", pa.float64()),
            ("mwmed", pa.float64()),
        ]
    ),
    "termicas": pa.schema(
        [
            ("codigo", pa.int32()),
            ("nome", pa.string()),
            ("submercado", pa.string()),
            ("estagio", pa.int32()),
            ("patamar", pa.int32()),
            ("inflex_mw", pa.float64()),
            ("disp_mw", pa.float64()),
            ("cvu", pa.float64()),
        ]
    ),
    "pequenas": pa.schema(
        [
            ("nome", pa.string()),
            ("submercado", pa.string()),
            ("fonte", pa.string()),
            ("mmgd", pa.bool_()),
            ("estagio", pa.int32()),
            ("patamar", pa.int32()),
            ("mwmed", pa.float64()),
        ]
    ),
    "ande": pa.schema([("estagio", pa.int32()), ("patamar", pa.int32()), ("mwmed", pa.float64())]),
    "renovaveis": pa.schema(
        [
            ("equivalente", pa.string()),
            ("submercado", pa.string()),
            ("fonte", pa.string()),
            ("estagio", pa.int32()),
            ("patamar", pa.int32()),
            ("mwmed", pa.float64()),
            ("capacidade_mw", pa.float64()),
        ]
    ),
    "volumes": pa.schema([("usina", pa.int32()), ("ree", pa.int32()), ("vol_ini_pct", pa.float64())]),
    "vazoes": pa.schema([("posto", pa.int32()), ("estagio", pa.int32()), ("m3s", pa.float64())]),
    "vazoes_cenarios": pa.schema(
        [
            ("posto", pa.int32()),
            ("estagio", pa.int32()),
            ("cenario", pa.int32()),
            ("probabilidade", pa.float64()),
            ("m3s", pa.float64()),
        ]
    ),
}


def _tabela(nome: str, linhas: list[dict]) -> pa.Table:
    esq = ESQUEMAS[nome]
    return pa.Table.from_pylist(linhas, schema=esq) if linhas else esq.empty_table()


def ler_deck(origem: str | Path) -> DeckDecomp:
    """Lê um deck DECOMP de uma pasta ou de um ZIP (como os do acervo do ONS)."""
    origem = Path(origem)
    arq = _Arquivos(origem)
    avisos: list[str] = []
    dadgers = arq.com_prefixo("dadger.")
    if not dadgers:
        raise DeckErro(f"{origem}: sem dadger.rvN")
    nome_dadger = dadgers[0]
    rev = nome_dadger.split(".", 1)[1]
    linhas = _linhas(arq.ler(nome_dadger) or b"")
    subs = _submercados(linhas)
    inicio = _data_inicio(linhas)
    te = _registros(linhas, "TE")
    titulo = te[0][4:].strip() if te else ""

    carga, horas = _carga(linhas, subs)
    if not horas:
        raise DeckErro("dadger sem horas por patamar no registro DP")
    renov = _renovaveis(arq.ler("renovaveis.csv"), subs)
    if not renov:
        avisos.append("sem renovaveis.csv: geração das usinas não despachadas ausente (decks antigos a trazem no PQ)")

    sem, cen, meta_vaz = [], [], {}
    bv = arq.ler(f"vazoes.{rev}")
    if bv:
        try:
            sem, cen, meta_vaz = ler_vazoes(bv)
        except (DeckErro, ValueError, IndexError) as e:
            avisos.append(f"vazoes.{rev} não lido: {e}")
    else:
        avisos.append(f"sem vazoes.{rev}")

    fcf = None
    cd, mc = arq.com_prefixo("cortdeco."), arq.com_prefixo("mapcut.")
    if cd and mc:
        fcf = {"cortdeco": arq.caminho(cd[0]), "mapcut": arq.caminho(mc[0])}
    else:
        avisos.append("sem cortdeco/mapcut: a FCF própria deste DECOMP precisa vir de uma rodada executada")

    deck = DeckDecomp(
        origem=str(origem),
        revisao=rev,
        titulo=titulo,
        inicio=inicio,
        estagios=_tabela("estagios", _estagios(inicio, horas)),
        carga=_tabela("carga", carga),
        termicas=_tabela("termicas", _termicas(linhas, subs)),
        pequenas=_tabela("pequenas", _pequenas(linhas, subs)),
        ande=_tabela("ande", _ande(linhas)),
        renovaveis=_tabela("renovaveis", renov),
        volumes=_tabela("volumes", _volumes(linhas)),
        vazoes=_tabela("vazoes", sem),
        vazoes_cenarios=_tabela("vazoes_cenarios", cen),
        fcf=fcf,
        avisos=avisos,
    )
    deck.avisos += [f"vazoes: {json.dumps(meta_vaz)}"] if meta_vaz else []
    return deck


def resumir(origem: str | Path, saida: str | Path) -> dict:
    """Grava um Parquet por tabela em `saida` e um resumo.json; devolve o resumo."""
    d = ler_deck(origem)
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    for nome, t in d.tabelas().items():
        pq.write_table(t, saida / f"{nome}.parquet")
    est = d.estagios.to_pylist()
    carga = d.carga.to_pylist()

    def media_estagio(linhas: list[dict], chave: str) -> dict:
        """MWmed do estágio por chave, ponderada pelas horas dos patamares."""
        horas = {(e["estagio"], p + 1): e[f"horas_{NOMES_PATAMAR[p]}"] or 0.0 for e in est for p in range(PATAMARES)}
        acc: dict = {}
        for r in linhas:
            h = horas.get((r["estagio"], r["patamar"]), 0.0)
            k = (r["estagio"], r[chave])
            a = acc.setdefault(k, [0.0, 0.0])
            a[0] += r["mwmed"] * h
            a[1] += h
        return {f"{e}|{c}": round(s / h, 1) for (e, c), (s, h) in sorted(acc.items()) if h}

    resumo = {
        "origem": d.origem,
        "revisao": d.revisao,
        "titulo": d.titulo,
        "inicio": d.inicio.isoformat(),
        "estagios": [{**e, "inicio": e["inicio"].isoformat(), "fim": e["fim"].isoformat()} for e in est],
        "linhas": {n: t.num_rows for n, t in d.tabelas().items()},
        "carga_mwmed_por_estagio_submercado": media_estagio(carga, "submercado"),
        "renovaveis_mwmed_por_estagio_fonte": media_estagio(d.renovaveis.to_pylist(), "fonte"),
        "mmgd_mwmed_por_estagio_fonte": media_estagio([r for r in d.pequenas.to_pylist() if r["mmgd"]], "fonte"),
        "termicas": {
            "usinas": len(set(d.termicas.column("codigo").to_pylist())),
            "disp_mw_estagio1": round(
                sum(r["disp_mw"] or 0 for r in d.termicas.to_pylist() if r["estagio"] == 1 and r["patamar"] == 1), 1
            ),
        },
        "fcf": d.fcf,
        "avisos": d.avisos,
    }
    (saida / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1) + "\n")
    return resumo


__all__ = ["DeckDecomp", "DeckErro", "ler_deck", "ler_vazoes", "resumir"]
