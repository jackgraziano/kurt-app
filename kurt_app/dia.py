"""O que o Kurt recebe para rodar um dia de um cenário: uma pasta com tudo resolvido.

``exportar_dia`` junta as escolhas de todas as abas para uma data e um cenário de
sorteio (0 = determinístico) e grava, do dia pedido até a sexta-feira da semana
operativa (a FCF do DECOMP vale no fim da semana; o gerador de deck usa o primeiro
dia em meias horas e os outros como o molde DESSEM pede):

- ``dia.json``: data, cenário, semana, deck, estágio, patamar de cada meia hora de cada dia,
  FCF (caminhos de cortdeco e mapcut e o estágio), opções e avisos;
- ``carga.parquet``: data, meia_hora, submercado, carga_mw, mmgd_mw, e a ANDE (submercado "ANDE");
- ``renovaveis.parquet``: data, usina_id, meia_hora, mw, cap_mw e cap_origem (eólicas e solares do
  caso de rede; com a opção 'ons' da aba Renováveis, cap_mw é a capacidade instalada do ONS);
- ``termicas.parquet``: linhas do CT da semana (com sobrescritos), por patamar;
- ``vazoes.parquet`` (posto, m3s) e ``volumes.parquet`` (usina, ree, vol_ini_pct);
- ``barras``, ``ramos``, ``usinas`` e ``restricoes.parquet``: a rede vigente na data;
- ``opcoes_deck.json``: o que o gerador faz com o que é só do dia do molde (ptoper, r11, operuh).

O gerador de deck do Kurt lê essa pasta e escreve o deck DESSEM do dia.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import abas, niveis, series
from .cenario import BASE
from .esquema import TABELAS
from .simulacao import SimulacaoErro, Simulacoes


def semana(sims: Simulacoes, s: dict, x: dict, cenario: int = 0) -> dict:
    """Séries da semana ``x`` (de ``niveis.semanas``) com as escolhas das abas."""
    caso = s.get("insumos", {}).get("rede", {}).get("caso", BASE)
    cad = sims.casos_rede.cadastro(caso)
    usinas = cad.renovaveis(data_rede(cad, x["inicio"]))
    op_ren = abas._opcoes(s, "renovaveis")

    def da_semana(aba: str, tabela: str) -> list[dict]:
        return [r for r in abas.linhas(s, aba, tabela) if r["semana"] == x["inicio"]]

    op_carga = abas._opcoes(s, "carga")
    op_exec = abas._opcoes(s, "execucao")
    mmgd = [dict(r, mmgd=True) for r in da_semana("carga", "mmgd")]
    renov = da_semana("renovaveis", "equivalentes")
    try:
        f = niveis.formas()
        f.tabela("carga")
    except FileNotFoundError as e:
        raise SimulacaoErro(f"formas históricas ausentes ({e})") from None
    usinas = capacidades(f, usinas, op_ren["capacidade"])
    mes = int(x["mes"][5:7]) if x.get("mes") else date.fromisoformat(x["inicio"]).month
    if op_ren["correcao_nivel"] == "historica":
        renov = corrigir_nivel(f, renov, mes)
    if op_carga["correcao_nivel"] == "historica":
        mmgd = corrigir_nivel(f, mmgd, mes, fonte_fixa="MMGD")
    sem = series.semana(
        f,
        x,
        da_semana("carga", "carga"),
        mmgd,
        da_semana("carga", "ande"),
        renov,
        usinas,
        {"ajuste_carga": op_carga["ajuste"], "renovaveis_nivel": op_ren["nivel"], "semente": op_exec["semente"]},
        cenario,
    )
    sem["usinas"] = {u["usina_id"]: u for u in usinas}
    return sem


def corrigir_nivel(f, linhas: list[dict], mes: int, fonte_fixa: str | None = None) -> list[dict]:
    """Multiplica o MWmed do DECOMP pela razão histórica previsão DESSEM / DECOMP (Formas.vies).

    Sem a tabela de viés (``vies_renovaveis.parquet``), nada muda.
    """
    vies = getattr(f, "vies", None)
    if vies is None:
        return linhas
    out = []
    for r in linhas:
        try:
            k = vies(fonte_fixa or r["fonte"], r["submercado"], mes, r["patamar"])
        except (KeyError, FileNotFoundError):
            k = 1.0
        out.append(dict(r, mwmed=(r["mwmed"] or 0.0) * (k or 1.0)))
    return out


def data_rede(cad, data: str) -> str:
    """Data para consultar a rede: antes da foto do caso base (backtest), vale a própria foto."""
    base = cad.data_base()
    return max(data, base) if base else data


def capacidades(f, usinas: list[dict], escolha: str) -> list[dict]:
    """Troca o cap_mw das EOL/UFV pela capacidade do ONS (ou estimada) quando a escolha é 'ons'."""
    out = []
    cap_ons = getattr(f, "capacidade", None)
    for u in usinas:
        u = dict(u, cap_origem="caso")
        if escolha == "ons" and cap_ons is not None:
            try:
                r = cap_ons(u["usina_id"])
            except (KeyError, FileNotFoundError):
                r = None
            if r and r[0]:
                u["cap_mw"], u["cap_origem"] = float(r[0]), r[1]
        out.append(u)
    return out


def _gravar_json(arq: Path, dados: dict) -> None:
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    tmp.replace(arq)


def _gravar(tabela: pa.Table, arq: Path) -> None:
    tmp = arq.with_suffix(".tmp")
    pq.write_table(tabela, tmp)
    tmp.replace(arq)


def exportar_dia(sims: Simulacoes, nome: str, data: str, cenario: int, saida: str | Path) -> dict:
    s = sims.ler(nome)
    d = date.fromisoformat(data)
    sems = niveis.semanas(s)
    x = niveis.semana_do_dia(sems, d)
    sem = semana(sims, s, x, cenario)
    i0 = sem["dias"].index(data)
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    mh = list(range(series.MEIAS))
    # do dia pedido até a sexta da semana operativa (a FCF vale no fim da semana)
    dias = list(range(i0, 7))

    def fatia(i: int) -> slice:
        return slice(i * series.MEIAS, (i + 1) * series.MEIAS)

    linhas = []
    for i in dias:
        dt = sem["dias"][i]
        for sub, v in sem["carga"].items():
            m = sem["mmgd"].get(sub, np.zeros_like(v))
            linhas += [
                {"data": dt, "meia_hora": h, "submercado": sub, "carga_mw": float(c), "mmgd_mw": float(g)}
                for h, c, g in zip(mh, v[fatia(i)], m[fatia(i)], strict=True)
            ]
        linhas += [
            {"data": dt, "meia_hora": h, "submercado": "ANDE", "carga_mw": float(c), "mmgd_mw": 0.0}
            for h, c in zip(mh, sem["ande"][fatia(i)], strict=True)
        ]
    _gravar(pa.Table.from_pylist(linhas), saida / "carga.parquet")
    _gravar(
        pa.Table.from_pylist(
            [
                {
                    "data": sem["dias"][i],
                    "usina_id": u,
                    "meia_hora": h,
                    "mw": float(v),
                    "cap_mw": float(sem["usinas"][u]["cap_mw"] or 0.0),
                    "cap_origem": sem["usinas"][u]["cap_origem"],
                }
                for i in dias
                for u, serie in sem["renovaveis"].items()
                for h, v in zip(mh, serie[fatia(i)], strict=True)
            ],
            schema=pa.schema(
                [
                    ("data", pa.string()),
                    ("usina_id", pa.string()),
                    ("meia_hora", pa.int32()),
                    ("mw", pa.float64()),
                    ("cap_mw", pa.float64()),
                    ("cap_origem", pa.string()),
                ]
            ),
        ),
        saida / "renovaveis.parquet",
    )
    for nome_arq, aba, tab in (("termicas", "termicas", "termicas"), ("vazoes", "hidro", "vazoes")):
        lin = [r for r in abas.linhas(s, aba, tab) if r["semana"] == x["inicio"]]
        _gravar(pa.Table.from_pylist(lin) if lin else pa.table({}), saida / f"{nome_arq}.parquet")
    vol = abas.linhas(s, "hidro", "volumes")
    _gravar(pa.Table.from_pylist(vol) if vol else pa.table({}), saida / "volumes.parquet")

    caso = s.get("insumos", {}).get("rede", {}).get("caso", BASE)
    cad = sims.casos_rede.cadastro(caso)
    conf = cad.configuracao(data_rede(cad, data))
    for ent in ("barras", "ramos", "usinas", "restricoes"):
        lin = conf[ent]
        _gravar(pa.Table.from_pylist(lin) if lin else TABELAS[ent].schema.empty_table(), saida / f"{ent}.parquet")

    fcf = next(f for f in abas.fcf_semanas(s) if f["semana"] == x["inicio"])
    avisos = [] if fcf["cortdeco"] else ["sem FCF nesta semana: o Kurt não roda sem ela (aba Valor da água)"]
    avisos += [
        f"renováveis {k}: fator {v} para bater com o DECOMP, limitado a {series.FAIXA_FATOR}"
        for k, v in sem["fator_fora_da_faixa"].items()
    ]
    info = {
        "cenario_simulacao": nome,
        "data": data,
        "cenario": cenario,
        "semana": x["inicio"],
        "deck": x["deck"],
        "estagio": x["estagio_usado"],
        "repetido": x["repetido"],
        "patamar": sem["patamar"][fatia(i0)].astype(int).tolist(),
        "dias": [{"data": sem["dias"][i], "patamar": sem["patamar"][fatia(i)].astype(int).tolist()} for i in dias],
        "fcf": fcf,
        "caso_rede": caso,
        "fator_renovaveis": sem["fator_renovaveis"],
        "fator_fora_da_faixa": sem["fator_fora_da_faixa"],
        "opcoes": {a: abas._opcoes(s, a) for a in abas.ABAS},
        "avisos": avisos,
    }
    op_exec = abas._opcoes(s, "execucao")
    _gravar_json(saida / "opcoes_deck.json", {k: op_exec[k] for k in ("ptoper", "r11", "operuh")})
    tmp = saida / "dia.json.tmp"
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1))
    tmp.replace(saida / "dia.json")
    return info


def resumo_semana(sims: Simulacoes, nome: str, inicio: str, cenario: int = 0) -> dict:
    """Séries agregadas de uma semana para os gráficos das abas (MW médio por meia hora)."""
    s = sims.ler(nome)
    x = next((w for w in niveis.semanas(s) if w["inicio"] == inicio), None)
    if x is None:
        raise SimulacaoErro(f"semana {inicio} não está no horizonte do cenário")
    sem = semana(sims, s, x, cenario)
    usinas = sem["usinas"]
    ren: dict[str, np.ndarray] = {}
    for uid, v in sem["renovaveis"].items():
        k = f"{usinas[uid]['fonte_energia']} {usinas[uid]['submercado']}"
        ren[k] = ren.get(k, 0) + v
    r = lambda v: np.round(v, 1).tolist()  # noqa: E731
    return {
        "semana": inicio,
        "dias": sem["dias"],
        "patamar": sem["patamar"].astype(int).tolist(),
        "carga": {k: r(v) for k, v in sem["carga"].items()},
        "mmgd": {k: r(v) for k, v in sem["mmgd"].items()},
        "ande": r(sem["ande"]),
        "renovaveis": {k: r(v) for k, v in sorted(ren.items())},
        "fator_renovaveis": sem["fator_renovaveis"],
        "fator_fora_da_faixa": sem["fator_fora_da_faixa"],
    }
