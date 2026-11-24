"""Importação de expansão prevista para eventos de cenário.

Dois CSV simples, pensados para serem preenchidos à mão ou a partir do
PAR/PEL e das outorgas da ANEEL:

linhas.csv
    data,de,para,tipo,circuitos,x_pct,cap_normal_mva,nome,fonte,confianca,nota
    2027-03-01,5350,5924,linha,1,1.2,2602,LT 500 kV Açu III – Jaguaruana II C2,PAR/PEL,previsto,

usinas.csv
    data,nome,fonte_energia,barra,cap_mw,flag_corte,fonte,confianca,nota
    2027-06-01,UFV Exemplo,UFV,5350,500,1,ANEEL,contratado,

`de`, `para` e `barra` aceitam o número da barra no deck (5350) ou o id do
cadastro (B5350). Cada linha do CSV vira um evento "entra"; com
circuitos > 1, um evento por circuito.
"""

from __future__ import annotations

import csv
import io


def _barra(v: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError("barra vazia")
    return f"B{v}" if v.isdigit() else v


def _num(v: str | None) -> float | None:
    v = (v or "").strip().replace(",", ".")
    return float(v) if v else None


def _comuns(r: dict) -> dict:
    return {
        "data": r["data"].strip(),
        "fonte": (r.get("fonte") or "usuario").strip(),
        "confianca": (r.get("confianca") or "previsto").strip(),
        "nota": (r.get("nota") or "").strip() or None,
    }


def eventos_de_linhas(texto: str) -> list[dict]:
    out = []
    for i, r in enumerate(csv.DictReader(io.StringIO(texto)), start=2):
        try:
            n = int(_num(r.get("circuitos")) or 1)
            for k in range(n):
                out.append(
                    {
                        **_comuns(r),
                        "acao": "entra",
                        "entidade": "ramos",
                        "registro": {
                            "de": _barra(r["de"]),
                            "para": _barra(r["para"]),
                            "tipo": (r.get("tipo") or "linha").strip(),
                            "circuito": None,
                            "x_pct": _num(r.get("x_pct")),
                            "cap_normal_mva": _num(r.get("cap_normal_mva")),
                            "nome": ((r.get("nome") or "").strip() or None)
                            and (r["nome"].strip() + (f" ({k + 1})" if n > 1 else "")),
                            "estado_base": "ligado",
                        },
                    }
                )
        except (KeyError, ValueError) as e:
            raise ValueError(f"linhas.csv, linha {i}: {e}") from e
    return out


def eventos_de_usinas(texto: str) -> list[dict]:
    out = []
    for i, r in enumerate(csv.DictReader(io.StringIO(texto)), start=2):
        try:
            out.append(
                {
                    **_comuns(r),
                    "acao": "entra",
                    "entidade": "usinas",
                    "registro": {
                        "nome": r["nome"].strip(),
                        "fonte_energia": r["fonte_energia"].strip().upper(),
                        "barra_id": _barra(r["barra"]),
                        "cap_mw": _num(r.get("cap_mw")),
                        "flag_corte": int(_num(r.get("flag_corte")) or 1),
                    },
                }
            )
        except (KeyError, ValueError) as e:
            raise ValueError(f"usinas.csv, linha {i}: {e}") from e
    return out
