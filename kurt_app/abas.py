"""Lógica e JSON das abas que vêm do DECOMP: carga, renováveis, térmicas, hidro,
valor da água e execução.

Cada aba guarda no cenário só as escolhas e o que o usuário mudou à mão:

    insumos[<aba>] = {"opcoes": {...}, "sobrescritos": {<tabela>: [linhas mudadas]}}

O JSON baixado traz as opções e as tabelas inteiras, já com os sobrescritos
aplicados. Na subida, cada linha é comparada com o DECOMP: o que for igual é
descartado, o que for diferente vira sobrescrito. Assim, baixar e subir sem editar
não muda nada, e mudar um número no JSON muda só aquele número.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import niveis
from .fontes.formas import TIPOS_DIA, datas
from .simulacao import SimulacaoErro, Simulacoes


@dataclass(frozen=True)
class Tabela:
    nome: str
    registro: str  # tabela de niveis.tabela, ou "volumes"
    chaves: tuple[str, ...]
    valores: tuple[str, ...]
    filtro: tuple[tuple[str, Any], ...] = ()
    rotulo: str = ""


def _linhas_decomp(s: dict, t: Tabela) -> list[dict]:
    linhas = niveis.volumes_iniciais(s) if t.registro == "volumes" else niveis.tabela(s, t.registro)
    linhas = [r for r in linhas if all(r.get(k) == v for k, v in t.filtro)]
    return [{k: r.get(k) for k in t.chaves + t.valores} for r in linhas]


def _chave(t: Tabela, r: dict) -> tuple:
    return tuple(r.get(k) for k in t.chaves)


def linhas(s: dict, aba: str, nome: str) -> list[dict]:
    """Linhas de uma tabela da aba, do DECOMP com os sobrescritos do usuário aplicados."""
    t = next(x for x in ABAS[aba].tabelas if x.nome == nome)
    sob = {_chave(t, r): r for r in (s.get("insumos", {}).get(aba) or {}).get("sobrescritos", {}).get(nome, [])}
    out = []
    for r in _linhas_decomp(s, t):
        m = sob.get(_chave(t, r))
        out.append({**r, **{k: m[k] for k in t.valores if k in m}} if m else r)
    return out


def _numero(v: Any, onde: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or math.isnan(v):
        raise SimulacaoErro(f"{onde}: esperado um número, veio {v!r}")
    return float(v)


def _sobrescritos(s: dict, t: Tabela, editadas: Any) -> list[dict]:
    if not isinstance(editadas, list) or not all(isinstance(r, dict) for r in editadas):
        raise SimulacaoErro(f"{t.nome}: esperada uma lista de objetos")
    originais = {_chave(t, r): r for r in _linhas_decomp(s, t)}
    out = []
    for i, r in enumerate(editadas):
        k = _chave(t, r)
        onde = f"{t.nome}, linha {i + 1} ({', '.join(f'{c}={v}' for c, v in zip(t.chaves, k, strict=True))})"
        if k not in originais:
            raise SimulacaoErro(f"{onde}: não existe no DECOMP do cenário; não mude as colunas de identificação")
        mud = {}
        for c in t.valores:
            if c not in r:
                continue
            if r[c] is None and originais[k][c] is None:
                continue
            v = _numero(r[c], f"{onde}, {c}")
            if v < 0 and c != "cvu":
                raise SimulacaoErro(f"{onde}, {c}: não pode ser negativo")
            if originais[k][c] is None or abs(v - originais[k][c]) > 1e-9:
                mud[c] = v
        if mud:
            out.append({**dict(zip(t.chaves, k, strict=True)), **mud})
    return out


# ---------------------------------------------------------------- opções de cada aba
def _escolha(op: dict, campo: str, permitidos: tuple, padrao: Any) -> Any:
    v = op.get(campo, padrao)
    if v not in permitidos:
        raise SimulacaoErro(f"opção {campo}: {v!r} não é válida (use {', '.join(map(str, permitidos))})")
    return v


def _inteiro(op: dict, campo: str, minimo: int, maximo: int, padrao: int) -> int:
    v = op.get(campo, padrao)
    if not isinstance(v, int) or isinstance(v, bool) or not minimo <= v <= maximo:
        raise SimulacaoErro(f"opção {campo}: use um inteiro de {minimo} a {maximo}")
    return v


def _op_carga(op: dict, _s: dict) -> dict:
    ajuste = op.get("ajuste", {}) or {}
    if not isinstance(ajuste, dict):
        raise SimulacaoErro('opção ajuste: use {"SE": 1.02, ...}')
    for k, v in ajuste.items():
        if k not in ("SE", "S", "NE", "N") or _numero(v, f"ajuste {k}") <= 0:
            raise SimulacaoErro(f"opção ajuste: {k}={v!r} inválido (submercados SE, S, NE, N; fator > 0)")
    sens = op.get("sensibilidade")
    if ajuste:  # formato antigo (fator por submercado): vira regra de nível da carga
        sens = dict(sens or {})
        sens["nivel"] = [
            *(sens.get("nivel") or []),
            *({"fonte": "CARGA", "submercado": k, "fator": float(v)} for k, v in ajuste.items()),
        ]
    return {
        "correcao_nivel": _escolha(op, "correcao_nivel", ("nenhuma", "historica"), "nenhuma"),
        "sensibilidade": _sensibilidade(sens, FONTES_CARGA),
    }


FONTES_SENS = ("EOL", "UFV", "MMGD")
FONTES_CARGA = ("CARGA",)
SUBS_SENS = ("todos", "SE", "S", "NE", "N")


def _lista_datas(v: Any, onde: str, mes: bool) -> list[str]:
    nome, fmt = ("meses", "AAAA-MM") if mes else ("datas", "AAAA-MM-DD")
    v = v or []
    if not isinstance(v, list) or any(not isinstance(m, str) for m in v):
        raise SimulacaoErro(f"{onde}: {nome} deve ser uma lista de {fmt}")
    try:
        for m in v:
            if len(m) != len(fmt):
                raise ValueError
            date.fromisoformat(m + "-01" if mes else m)
    except ValueError:
        raise SimulacaoErro(f"{onde}: {nome} deve ser uma lista de {fmt} ({m!r} inválido)") from None
    return sorted(set(v))


def _sensibilidade(sens: Any, fontes: tuple = FONTES_SENS) -> dict:
    if sens in (None, {}):
        return {"nivel": [], "forma": {}}
    if not isinstance(sens, dict):
        raise SimulacaoErro('opção sensibilidade: use {"nivel": [...], "forma": {...}}')
    nivel = []
    for i, r in enumerate(sens.get("nivel") or []):
        onde = f"sensibilidade.nivel, item {i + 1}"
        if not isinstance(r, dict) or r.get("fonte") not in fontes:
            raise SimulacaoErro(f"{onde}: fonte deve ser {', '.join(fontes)}")
        sub = r.get("submercado", "todos")
        if sub not in SUBS_SENS:
            raise SimulacaoErro(f"{onde}: submercado deve ser {', '.join(SUBS_SENS)}")
        fator = _numero(r.get("fator"), f"{onde}, fator")
        if not 0 <= fator <= 5:
            raise SimulacaoErro(f"{onde}: fator {fator} fora de 0 a 5 (1 = sem mudança)")
        meses = _lista_datas(r.get("meses"), onde, True)
        dts = _lista_datas(r.get("datas"), onde, False)
        if meses and dts:
            raise SimulacaoErro(f"{onde}: use meses ou datas, não os dois na mesma regra")
        if abs(fator - 1) > 1e-9:
            extra = {"meses": meses} if meses else {"datas": dts} if dts else {}
            nivel.append({"fonte": r["fonte"], "submercado": sub, "fator": fator, **extra})
    forma = {}
    for fonte, pesos in (sens.get("forma") or {}).items():
        if fonte not in fontes:
            raise SimulacaoErro(f"sensibilidade.forma: fonte {fonte!r} inválida (use {', '.join(fontes)})")
        if not isinstance(pesos, list) or len(pesos) != 24:
            raise SimulacaoErro(f"sensibilidade.forma.{fonte}: use 24 pesos, um por hora (1 = sem mudança)")
        w = [_numero(v, f"sensibilidade.forma.{fonte}") for v in pesos]
        if any(v < 0 or v > 5 for v in w) or not any(w):
            raise SimulacaoErro(f"sensibilidade.forma.{fonte}: pesos de 0 a 5, nem todos zero")
        if any(abs(v - 1) > 1e-9 for v in w):
            forma[fonte] = w
    return {"nivel": nivel, "forma": forma}


def _op_renovaveis(op: dict, _s: dict) -> dict:
    return {
        "nivel": _escolha(op, "nivel", ("decomp", "capacidade"), "decomp"),
        "capacidade": _escolha(op, "capacidade", ("ons", "caso"), "ons"),
        "correcao_nivel": _escolha(op, "correcao_nivel", ("nenhuma", "historica"), "nenhuma"),
        "sensibilidade": _sensibilidade(op.get("sensibilidade")),
    }


def _op_termicas(op: dict, _s: dict) -> dict:
    return {"estado_inicial": _escolha(op, "estado_inicial", ("encadear", "heuristica"), "heuristica")}


def _op_hidro(op: dict, _s: dict) -> dict:
    return {"vazoes": _escolha(op, "vazoes", ("deterministica", "cenarios_gevazp"), "deterministica")}


def _op_valor_agua(op: dict, _s: dict) -> dict:
    origem = _escolha(op, "origem", ("decks", "arquivo"), "decks")
    out = {"origem": origem}
    if origem == "arquivo":
        for c in ("cortdeco", "mapcut"):
            if not op.get(c) or not Path(op[c]).is_file():
                raise SimulacaoErro(f"opção {c}: arquivo {op.get(c)!r} não encontrado na máquina do servidor")
            out[c] = op[c]
    return out


def _op_execucao(op: dict, s: dict) -> dict:
    dias = _escolha(op, "dias", ("representativos", "todos"), "representativos")
    tipos = op.get("tipos", list(TIPOS_DIA))
    if not isinstance(tipos, list) or not tipos or any(t not in TIPOS_DIA for t in tipos):
        raise SimulacaoErro(f"opção tipos: lista com {', '.join(TIPOS_DIA)}")
    return {
        "dias": dias,
        "tipos": [t for t in TIPOS_DIA if t in tipos],
        "cenarios": _inteiro(op, "cenarios", 1, 200, 5),
        "semente": _inteiro(op, "semente", 0, 2**31 - 1, 0),
        "discretizacao": _escolha(op, "discretizacao", ("semi-horaria", "horaria"), "semi-horaria"),
        "paralelo": _inteiro(op, "paralelo", 1, 64, 16),
        "rede": bool(op.get("rede", True)),
        # o que o gerador do deck do dia faz com o que é só do dia do molde DESSEM
        "ptoper": _escolha(op, "ptoper", ("retirar", "manter"), "retirar"),
        "r11": _escolha(op, "r11", ("retirar", "manter"), "retirar"),
        "operuh": _escolha(op, "operuh", ("so_defluencia", "manter", "retirar"), "so_defluencia"),
    }


def plano(s: dict) -> list[dict]:
    """Dias que serão rodados e o peso de cada um (dias do mês que ele representa)."""
    op = _op_execucao((s.get("insumos", {}).get("execucao") or {}).get("opcoes", {}), s)
    ini, fim = s["inicio"], s["fim"]
    meses = sorted({d.isoformat()[:7] for d in _intervalo(ini, fim)})
    out = []
    for m in meses:
        no_horizonte = [d for d in datas(m) if ini <= d.isoformat() <= fim]
        if op["dias"] == "todos":
            out += [{"data": d.isoformat(), "peso": 1.0, "tipo": None} for d in no_horizonte]
            continue
        for t in op["tipos"]:
            do_tipo = [d for d in no_horizonte if d in set(datas(m, t))]
            if do_tipo:
                # o dia do meio do mês, que evita a virada de deck da primeira semana
                out.append({"data": do_tipo[len(do_tipo) // 2].isoformat(), "peso": float(len(do_tipo)), "tipo": t})
    return [dict(x, cenarios=op["cenarios"]) for x in out]


def _intervalo(ini: str, fim: str) -> list[date]:
    a, b = date.fromisoformat(ini), date.fromisoformat(fim)
    return [date.fromordinal(o) for o in range(a.toordinal(), b.toordinal() + 1)]


# ---------------------------------------------------------------- registro das abas
@dataclass(frozen=True)
class Aba:
    tabelas: tuple[Tabela, ...]
    opcoes: Any  # (op, s) -> op validadas
    ajuda: tuple[str, ...]


PAT = "patamar 1 = pesado, 2 = médio, 3 = leve"
AJUDA_COMUM = (
    "Os números vêm dos decks DECOMP da aba Decks. Mude o que quiser nas tabelas e suba o JSON:"
    " só as diferenças ficam guardadas, e o resto continua seguindo o DECOMP.",
    "Não mude as colunas de identificação (semana, submercado, código...). Linhas apagadas voltam ao DECOMP.",
    "'semana' é o sábado de início da semana operativa.",
)

ABAS: dict[str, Aba] = {
    "carga": Aba(
        (
            Tabela("carga", "carga", ("semana", "submercado", "patamar"), ("mwmed",), rotulo="Carga (DP)"),
            Tabela("ande", "ande", ("semana", "patamar"), ("mwmed",), rotulo="ANDE (RI de Itaipu)"),
            Tabela(
                "mmgd",
                "pequenas",
                ("semana", "submercado", "fonte", "patamar"),
                ("mwmed",),
                (("mmgd", True),),
                "MMGD (PQ)",
            ),
        ),
        _op_carga,
        (
            *AJUDA_COMUM,
            f"carga: MWmed por submercado e patamar ({PAT}). A forma semi-horária vem do histórico do mês e tipo de dia.",
            "opcoes.sensibilidade.nivel: lista de {fonte: CARGA, submercado: todos | SE | S | NE | N, fator,"
            ' meses (opcional, ["2027-01"]) ou datas (opcional, ["2027-01-15"])}; ex.: fator 1.03 = 3% mais carga.'
            " As regras se multiplicam. A tela de energia grava aqui os alvos em GWmed por mês, semana ou dia.",
            "opcoes.sensibilidade.forma: {CARGA: 24 pesos horários}; muda a forma do dia sem mudar a energia.",
        ),
    ),
    "renovaveis": Aba(
        (
            Tabela(
                "equivalentes",
                "renovaveis",
                ("semana", "equivalente", "submercado", "fonte", "patamar"),
                ("mwmed",),
                rotulo="Renováveis equivalentes (renovaveis.csv)",
            ),
        ),
        _op_renovaveis,
        (
            *AJUDA_COMUM,
            f"equivalentes: geração eólica e solar do DECOMP por região e fonte ({PAT}).",
            "opcoes.nivel: 'decomp' escala as usinas do caso de rede para somar o equivalente; 'capacidade' usa só"
            " capacidade × fator de capacidade histórico (útil quando a expansão do caso de rede vai além do DECOMP).",
            "opcoes.capacidade: 'ons' usa a capacidade instalada do ONS (ou a estimada de vários dias de previsão)"
            " quando a usina casou; 'caso' usa o cap_mw do caso de rede (no caso base, o máximo da previsão de um dia).",
            "opcoes.correcao_nivel: 'historica' multiplica os equivalentes do DECOMP pela razão histórica entre a"
            " previsão do DESSEM e o DECOMP (por fonte, submercado, mês e patamar); 'nenhuma' usa o DECOMP.",
            "opcoes.sensibilidade.nivel: lista de {fonte: EOL | UFV | MMGD, submercado: todos | SE | S | NE | N,"
            ' fator, meses (opcional, ["2027-01"]) ou datas (opcional, ["2027-01-15"])}; ex.: fator 1.1 = 10% mais'
            " geração. As regras se multiplicam. A tela de energia grava aqui os alvos em GWmed por período.",
            "opcoes.sensibilidade.forma: {fonte: 24 pesos horários}; muda a forma do dia sem mudar a energia,"
            " ex.: pesos maiores das 10 às 15 h concentram a solar no meio-dia. Nenhuma usina passa da capacidade.",
        ),
    ),
    "termicas": Aba(
        (
            Tabela(
                "termicas",
                "termicas",
                ("semana", "codigo", "nome", "submercado", "patamar"),
                ("inflex_mw", "disp_mw", "cvu"),
                rotulo="Térmicas (CT)",
            ),
        ),
        _op_termicas,
        (
            *AJUDA_COMUM,
            f"termicas: inflexibilidade, disponibilidade (MW) e CVU (R$/MWh) por patamar ({PAT}).",
            "opcoes.estado_inicial: 'heuristica' liga no início do dia as térmicas com inflexibilidade > 0;"
            " 'encadear' usa o estado do fim do dia anterior quando a execução roda dias seguidos.",
            "Unidades, rampas e tempos mínimos vêm do deck DESSEM do caso base. Manutenções são ignoradas.",
        ),
    ),
    "hidro": Aba(
        (
            Tabela("vazoes", "vazoes", ("semana", "posto"), ("m3s",), rotulo="Vazões incrementais (vazoes.rvN)"),
            Tabela("volumes", "volumes", ("usina", "ree"), ("vol_ini_pct",), rotulo="Volume inicial (UH)"),
        ),
        _op_hidro,
        (
            *AJUDA_COMUM,
            "vazoes: vazão incremental semanal por posto (m³/s), constante dentro da semana.",
            "volumes: volume inicial em % do útil, do deck da primeira semana.",
            "opcoes.vazoes: 'deterministica' (previsão do deck) ou 'cenarios_gevazp' (cenários do mês seguinte,"
            " quando o deck os traz).",
        ),
    ),
    "valor_agua": Aba(
        (),
        _op_valor_agua,
        (
            "opcoes.origem: 'decks' usa a FCF (cortdeco e mapcut) de cada deck da aba Decks, no estágio da semana;"
            " 'arquivo' usa um único par cortdeco/mapcut (opcoes.cortdeco e opcoes.mapcut, caminhos na máquina).",
            "'semanas' é só informativo: mostra de onde vem a FCF de cada semana.",
        ),
    ),
    "execucao": Aba(
        (),
        _op_execucao,
        (
            "opcoes.dias: 'representativos' roda, por mês, um dia de cada tipo em 'tipos' (util, sabado, domingo),"
            " com peso igual ao número de dias daquele tipo; 'todos' roda todos os dias.",
            "opcoes.cenarios: quantos cenários de carga e renováveis sortear (dias históricos); semente fixa o sorteio.",
            "opcoes.discretizacao, paralelo (rodadas ao mesmo tempo) e rede (com ou sem a rede elétrica).",
            "O deck de cada dia usa como molde o deck DESSEM do ONS mais recente com o mesmo dia da semana."
            " opcoes.ptoper e r11 (retirar | manter): pontos de operação das térmicas e régua 11 de Itaipu do dia"
            " do molde; opcoes.operuh: so_defluencia (mantém só as restrições de defluência), manter ou retirar.",
            "'plano' é só informativo: os dias que serão rodados e o peso de cada um.",
        ),
    ),
}


def _opcoes(s: dict, aba: str) -> dict:
    return ABAS[aba].opcoes((s.get("insumos", {}).get(aba) or {}).get("opcoes", {}) or {}, s)


def fcf_semanas(s: dict) -> list[dict]:
    op = _opcoes(s, "valor_agua")
    out = []
    for x in niveis.semanas(s):
        if op["origem"] == "arquivo":
            out.append({"semana": x["inicio"], "cortdeco": op["cortdeco"], "mapcut": op["mapcut"], "estagio": None})
            continue
        fcf = niveis.deck(x["caminho"]).fcf
        out.append(
            {
                "semana": x["inicio"],
                "deck": x["deck"],
                "estagio": x["estagio_usado"],
                "cortdeco": fcf["cortdeco"] if fcf else None,
                "mapcut": fcf["mapcut"] if fcf else None,
            }
        )
    return out


def exportar(aba: str):
    def f(_sims: Simulacoes, s: dict) -> tuple[dict, list[str]]:
        dados: dict[str, Any] = {"opcoes": _opcoes(s, aba)}
        for t in ABAS[aba].tabelas:
            dados[t.nome] = linhas(s, aba, t.nome)
        if aba == "valor_agua":
            dados["semanas"] = fcf_semanas(s)
            sem = [x["semana"] for x in dados["semanas"] if not x["cortdeco"]]
            if sem:
                dados["avisos"] = [f"Sem FCF em {len(sem)} semanas (a partir de {sem[0]}): o Kurt não roda sem ela."]
        if aba == "execucao":
            dados["plano"] = plano(s)
            n = sum(x["cenarios"] for x in dados["plano"])
            dados["resumo"] = {"dias": len(dados["plano"]), "rodadas": n}
        return dados, list(ABAS[aba].ajuda)

    return f


def importar(aba: str):
    def f(sims: Simulacoes, s: dict, dados: dict) -> dict:
        op = ABAS[aba].opcoes(dados.get("opcoes", {}) or {}, s)
        sob = {}
        for t in ABAS[aba].tabelas:
            if t.nome in dados:
                mud = _sobrescritos(s, t, dados[t.nome])
                if mud:
                    sob[t.nome] = mud
        return sims.atualizar(s["nome"], {"insumos": {aba: {"opcoes": op, "sobrescritos": sob}}})

    return f
