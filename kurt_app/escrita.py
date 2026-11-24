"""Escrita de um cadastro a partir de listas de dicionários."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .esquema import TABELAS


def _normaliza(valor: Any, tipo: pa.DataType, json_col: bool) -> Any:
    if valor is None:
        return None
    if json_col and not isinstance(valor, str):
        return json.dumps(valor, ensure_ascii=False)
    if pa.types.is_date32(tipo) and isinstance(valor, str):
        return date.fromisoformat(valor)
    return valor


def tabela(nome: str, linhas: list[dict[str, Any]]) -> pa.Table:
    t = TABELAS[nome]
    extras = {k for r in linhas for k in r} - set(t.nomes)
    if extras:
        raise ValueError(f"{nome}: colunas fora do esquema: {sorted(extras)}")
    cols = {c: pa.array([_normaliza(r.get(c), tipo, c in t.json) for r in linhas], type=tipo) for c, tipo in t.colunas}
    return pa.Table.from_pydict(cols, schema=t.schema)


def escrever(pasta: str | Path, tabelas: dict[str, pa.Table], info: dict[str, Any] | None = None) -> Path:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, t in tabelas.items():
        if nome not in TABELAS:
            raise ValueError(f"tabela desconhecida: {nome}")
        pq.write_table(t.cast(TABELAS[nome].schema), pasta / f"{nome}.parquet")
    if info is not None:
        (pasta / "cadastro.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    return pasta
