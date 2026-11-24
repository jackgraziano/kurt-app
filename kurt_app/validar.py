"""Checagens de qualidade do cadastro.

Cada checagem devolve problemas com severidade:
- "erro": o cadastro está inconsistente e não deve ser usado para gerar deck;
- "aviso": merece revisão, mas não impede o uso.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date

import pyarrow as pa

from .cadastro import Cadastro
from .esquema import ACOES, CONFIANCAS, ENTIDADES, TABELAS


@dataclass
class Problema:
    severidade: str  # erro | aviso
    checagem: str
    tabela: str
    detalhe: str


def _rows(t: pa.Table) -> list[dict]:
    return t.to_pylist()


def _versoes_sem_sobreposicao(cad: Cadastro) -> list[Problema]:
    out = []
    for nome in ENTIDADES:
        tab = TABELAS[nome]
        por_id: dict[str, list[dict]] = defaultdict(list)
        for r in _rows(cad.tabelas[nome]):
            por_id[r[tab.chave]].append(r)
        for ident, vs in por_id.items():
            vs.sort(key=lambda r: r["vigente_de"])
            for v in vs:
                if v["vigente_ate"] is not None and v["vigente_ate"] <= v["vigente_de"]:
                    out.append(Problema("erro", "vigencia", nome, f"{ident}: vigente_ate <= vigente_de"))
            for v1, v2 in itertools.pairwise(vs):
                if v1["vigente_ate"] is None or v1["vigente_ate"] > v2["vigente_de"]:
                    out.append(
                        Problema(
                            "erro",
                            "vigencia",
                            nome,
                            f"{ident}: versões sobrepostas ({v1['vigente_de']} e {v2['vigente_de']})",
                        )
                    )
    return out


def _dominios(cad: Cadastro) -> list[Problema]:
    out = []
    for nome in ENTIDADES:
        for r in _rows(cad.tabelas[nome]):
            if r["confianca"] not in CONFIANCAS:
                out.append(Problema("erro", "dominio", nome, f"{r[TABELAS[nome].chave]}: confianca '{r['confianca']}'"))
    for r in _rows(cad.tabelas["eventos"]):
        if r["acao"] not in ACOES:
            out.append(Problema("erro", "dominio", "eventos", f"{r['evento_id']}: acao '{r['acao']}'"))
        if r["entidade"] not in ENTIDADES:
            out.append(Problema("erro", "dominio", "eventos", f"{r['evento_id']}: entidade '{r['entidade']}'"))
        if r["confianca"] not in CONFIANCAS:
            out.append(Problema("erro", "dominio", "eventos", f"{r['evento_id']}: confianca '{r['confianca']}'"))
    for nome in ("usinas", "restricoes", "eventos"):
        for c in TABELAS[nome].json:
            for r in _rows(cad.tabelas[nome]):
                v = r.get(c)
                if v is None:
                    continue
                try:
                    json.loads(v)
                except ValueError:
                    out.append(Problema("erro", "json", nome, f"{r[TABELAS[nome].chave]}: '{c}' não é JSON"))
    return out


def _datas_de_corte(cad: Cadastro) -> list[date]:
    """Datas em que algo muda: cada início de vigência."""
    ds = set()
    for nome in ENTIDADES:
        for r in _rows(cad.tabelas[nome]):
            ds.add(r["vigente_de"])
    return sorted(ds)


def _referencias_e_conexidade(cad: Cadastro) -> list[Problema]:
    out = []
    for d in _datas_de_corte(cad):
        ds = d.isoformat()
        cfg = cad.configuracao(ds)
        ids = {b["barra_id"] for b in cfg["barras"]}
        # referências: o filtro de configuracao() já descarta ramos e usinas sem barra,
        # então a checagem é feita nas tabelas brutas vigentes.
        for nome, campos in (("ramos", ("de", "para")), ("usinas", ("barra_id",))):
            tab = TABELAS[nome]
            for r in _rows(cad.tabelas[nome]):
                if r["vigente_de"] <= d and (r["vigente_ate"] is None or r["vigente_ate"] > d):
                    for c in campos:
                        if r[c] not in ids:
                            out.append(
                                Problema(
                                    "erro", "referencia", nome, f"{ds}: {r[tab.chave]}.{c} = {r[c]} sem barra vigente"
                                )
                            )
        # conexidade: barras com usina ou carga não podem ficar isoladas
        viz: dict[str, set[str]] = defaultdict(set)
        for r in cfg["ramos"]:
            if r.get("estado_base", "ligado") != "desligado":
                viz[r["de"]].add(r["para"])
                viz[r["para"]].add(r["de"])
        com_injecao = {u["barra_id"] for u in cfg["usinas"]} | {
            b["barra_id"]
            for b in cfg["barras"]
            if any((b.get(k) or 0) > 0 for k in ("carga_leve_mw", "carga_media_mw", "carga_pesada_mw"))
        }
        isoladas = sorted(b for b in com_injecao if b in ids and not viz.get(b))
        if isoladas:
            out.append(
                Problema("erro", "conexidade", "barras", f"{ds}: barras com usina ou carga isoladas: {isoladas[:10]}")
            )
        # componentes: mais de uma ilha é aviso (recortes regionais são esperados)
        vistos: set[str] = set()
        ilhas = 0
        for b in ids:
            if b in vistos:
                continue
            ilhas += 1
            pilha = [b]
            while pilha:
                x = pilha.pop()
                if x in vistos:
                    continue
                vistos.add(x)
                pilha.extend(viz.get(x, ()))
        if ilhas > 1:
            out.append(Problema("aviso", "conexidade", "barras", f"{ds}: a rede tem {ilhas} ilhas"))
    return out


def _eventos_apontam(cad: Cadastro) -> list[Problema]:
    out = []
    existentes = {nome: {r[TABELAS[nome].chave] for r in _rows(cad.tabelas[nome])} for nome in ENTIDADES}
    for r in _rows(cad.tabelas["eventos"]):
        if r["entidade"] in existentes and r["entidade_id"] not in existentes[r["entidade"]]:
            out.append(
                Problema(
                    "aviso", "eventos", "eventos", f"{r['evento_id']}: {r['entidade_id']} não existe em {r['entidade']}"
                )
            )
    return out


def validar(cad: Cadastro) -> list[Problema]:
    probs: list[Problema] = []
    probs += _versoes_sem_sobreposicao(cad)
    probs += _dominios(cad)
    probs += _referencias_e_conexidade(cad)
    probs += _eventos_apontam(cad)
    return probs


def relatorio(probs: list[Problema]) -> dict:
    return {
        "erros": sum(p.severidade == "erro" for p in probs),
        "avisos": sum(p.severidade == "aviso" for p in probs),
        "problemas": [asdict(p) for p in probs],
    }
