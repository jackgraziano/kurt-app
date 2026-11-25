"""Níveis semanais do cenário, tirados dos decks DECOMP escolhidos na aba Decks.

Para cada semana operativa do horizonte, a aba Decks diz qual deck e qual estágio
valem. Este módulo lê os decks (com cache em memória) e devolve, por semana, as
linhas do estágio certo de cada registro: carga (DP), térmicas (CT), MMGD (PQ),
ANDE (RI), renováveis equivalentes (renovaveis.csv), vazões e volumes.

Também guarda onde ficam as formas históricas (``KURT_APP_FORMAS``, padrão
``dados/formas``), que as abas usam para dar forma horária aos níveis.
"""

from __future__ import annotations

import os
import threading
from datetime import date
from functools import cache
from pathlib import Path

from .fontes.decomp import NOMES_PATAMAR, DeckDecomp, DeckErro, ler_deck
from .fontes.formas import Formas
from .simulacao import SimulacaoErro

RAIZ = Path(__file__).resolve().parent.parent
REGISTROS = ("carga", "termicas", "pequenas", "ande", "renovaveis", "vazoes")

_trava = threading.Lock()
_decks: dict[str, tuple[float, DeckDecomp]] = {}


def deck(caminho: str) -> DeckDecomp:
    """Lê um deck (pasta ou zip), reaproveitando a leitura enquanto o arquivo não mudar."""
    p = Path(caminho)
    if not p.exists():
        raise SimulacaoErro(f"deck {caminho} não existe mais: refaça a aba Decks")
    mtime = p.stat().st_mtime
    with _trava:
        if caminho in _decks and _decks[caminho][0] == mtime:
            return _decks[caminho][1]
    try:
        d = ler_deck(p)
    except DeckErro as e:
        raise SimulacaoErro(f"deck {p.name}: {e}") from None
    with _trava:
        _decks[caminho] = (mtime, d)
    return d


@cache
def _formas(pasta: str) -> Formas:
    return Formas(pasta)


def formas() -> Formas:
    return _formas(os.environ.get("KURT_APP_FORMAS") or str(RAIZ / "dados" / "formas"))


def semanas(s: dict) -> list[dict]:
    """Semanas do cenário com o deck (caminho) e o estágio semanal efetivo de cada uma."""
    ins = s.get("insumos", {}).get("decks") or {}
    sem = ins.get("semanas") or []
    if not sem:
        raise SimulacaoErro("escolha os decks DECOMP na aba Decks antes desta aba")
    caminhos = ins.get("caminhos") or {}
    out = []
    for x in sem:
        if x.get("deck") not in caminhos:
            raise SimulacaoErro(f"semana {x['inicio']}: deck {x.get('deck')!r} sem caminho; salve a aba Decks de novo")
        d = deck(caminhos[x["deck"]])
        semanais = [e["estagio"] for e in d.estagios.to_pylist() if e["dias"] == 7]
        est = x["estagio"] if x["estagio"] in semanais else (max(semanais) if semanais else 1)
        horas = next(e for e in d.estagios.to_pylist() if e["estagio"] == est)
        out.append(
            dict(
                x,
                caminho=caminhos[x["deck"]],
                estagio_usado=est,
                horas=[horas[f"horas_{n}"] for n in NOMES_PATAMAR],
            )
        )
    return out


def tabela(s: dict, registro: str, sems: list[dict] | None = None) -> list[dict]:
    """Linhas de um registro, semana a semana, com a coluna 'semana' (início, AAAA-MM-DD)."""
    if registro not in REGISTROS:
        raise KeyError(registro)
    out = []
    for x in sems or semanas(s):
        d = deck(x["caminho"])
        for r in getattr(d, registro).to_pylist():
            if r["estagio"] == x["estagio_usado"]:
                out.append({"semana": x["inicio"], **{k: v for k, v in r.items() if k != "estagio"}})
    return out


def volumes_iniciais(s: dict) -> list[dict]:
    """Volumes do registro UH do deck da primeira semana."""
    x = semanas(s)[0]
    return deck(x["caminho"]).volumes.to_pylist()


def semana_do_dia(sems: list[dict], d: date) -> dict:
    iso = d.isoformat()
    for x in sems:
        if x["inicio"] <= iso <= x["fim"]:
            return x
    raise SimulacaoErro(f"{iso} está fora do horizonte do cenário")
