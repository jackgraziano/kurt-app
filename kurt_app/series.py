"""Séries semi-horárias de uma semana: o nível vem do DECOMP, a forma do histórico.

Regra de calibração, comum a carga, MMGD e renováveis:

1. monta-se a forma da semana (7 dias × 48 meias horas) com os perfis históricos do
   mês e do tipo de dia de cada dia (útil, sábado, domingo/feriado);
2. as meias horas da semana são classificadas nos patamares do DECOMP pela carga do
   SIN: as 2×H_pesada meias horas de maior carga são o patamar pesado, as 2×H_média
   seguintes o médio, o resto o leve (H vem do registro DP do deck);
3. carga e ANDE: dentro de cada patamar, a série é escalada para que a média bata com
   o MWmed do DECOMP. A média semanal bate, e a diferença entre dias úteis e fim de
   semana vem do histórico;
4. MMGD e renováveis: a série é escalada por um fator único na semana, para que a
   energia da semana bata com a do DECOMP (soma de MWmed × horas dos patamares). Um
   fator por patamar distorceria a forma solar (um patamar quase todo noturno teria
   fator enorme nas poucas meias horas com sol).

Cenários (``cenario`` >= 1) sorteiam, para cada dia, um dia histórico do mesmo mês e
tipo e multiplicam a série determinística pela razão entre aquele dia e o dia típico.
Como o dia típico é a média dos dias históricos, a média do conjunto fica no DECOMP.
"""

from __future__ import annotations

import zlib
from datetime import date, timedelta

import numpy as np

from .fontes.formas import SUBMERCADOS, Formas, tipo_dia

MEIAS = 48
PATAMARES = (1, 2, 3)
RAZAO_MAX = 3.0


def dias_da_semana(inicio: str) -> list[date]:
    d0 = date.fromisoformat(inicio)
    return [d0 + timedelta(days=i) for i in range(7)]


def patamares(perfil: np.ndarray, horas: list[float]) -> np.ndarray:
    """Patamar (1 pesado, 2 médio, 3 leve) de cada meia hora, pela ordem decrescente do perfil."""
    n = len(perfil)
    total = sum(horas) or 1.0
    n_p = round(n * horas[0] / total)
    n_m = round(n * horas[1] / total)
    ordem = np.argsort(-perfil, kind="stable")
    pat = np.full(n, 3, dtype=np.int8)
    pat[ordem[:n_p]] = 1
    pat[ordem[n_p : n_p + n_m]] = 2
    return pat


def ajustar(serie: np.ndarray, pat: np.ndarray, alvo: dict[int, float]) -> np.ndarray:
    """Escala a série em cada patamar para que a média dê o alvo (perfil nulo vira constante)."""
    out = serie.astype(float).copy()
    for p, v in alvo.items():
        m = pat == p
        if not m.any():
            continue
        media = out[m].mean()
        out[m] = out[m] * (v / media) if media > 1e-9 else v
    return out


def ajustar_total(serie: np.ndarray, pat: np.ndarray, alvo: dict[int, float]) -> np.ndarray:
    """Escala a série por um fator único para que a energia bata com a do alvo por patamar."""
    energia = sum(v * (pat == p).sum() for p, v in alvo.items())
    total = float(serie.sum())
    if total > 1e-9:
        return serie * (energia / total)
    return np.full(len(serie), energia / len(serie))


def _por_patamar(linhas: list[dict], filtro: dict) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in linhas:
        if all(r.get(k) == v for k, v in filtro.items()):
            out[r["patamar"]] = out.get(r["patamar"], 0.0) + (r["mwmed"] or 0.0)
    return out


# ---------------------------------------------------------------- sorteio de dias históricos
def dias_sorteados(f: Formas, inicio: str, cenario: int, semente: int = 0) -> list[dict | None]:
    """Para cada dia da semana, um dia histórico do mesmo mês e tipo (None no cenário 0)."""
    if cenario <= 0:
        return [None] * 7
    out = []
    for d in dias_da_semana(inicio):
        t = f.dias(d.month, tipo_dia(d))
        if t.num_rows == 0:
            out.append(None)
            continue
        rng = np.random.default_rng(zlib.crc32(f"{semente}|{cenario}|{d.isoformat()}".encode()))
        out.append(t.slice(int(rng.integers(t.num_rows)), 1).to_pylist()[0])
    return out


def _tipico(f: Formas, mes: int, tipo: str | None, coluna: str) -> np.ndarray | None:
    t = f.dias(mes, tipo)
    if coluna not in t.column_names:
        return None
    vals = [v for v in t[coluna].to_pylist() if v is not None]
    return np.mean(np.array(vals, dtype=float), axis=0) if vals else None


def _razao(hist: list[float] | None, tipico: np.ndarray | None) -> np.ndarray:
    if hist is None or tipico is None:
        return np.ones(MEIAS)
    h = np.array(hist, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(tipico > 1e-3, h / tipico, 1.0)
    return np.clip(r, 0.0, RAZAO_MAX)


# ---------------------------------------------------------------- semana inteira
def semana(
    f: Formas,
    x: dict,
    carga: list[dict],
    pequenas: list[dict],
    ande: list[dict],
    renovaveis: list[dict],
    usinas: list[dict],
    opcoes: dict | None = None,
    cenario: int = 0,
) -> dict:
    """Séries da semana ``x`` (uma entrada de ``niveis.semanas``), 336 meias horas cada.

    ``carga``, ``pequenas``, ``ande`` e ``renovaveis`` são as linhas do DECOMP já filtradas
    para a semana. ``usinas`` são as eólicas e solares vigentes (usina_id, fonte_energia,
    submercado, cap_mw, uf). ``opcoes``: ``ajuste_carga`` {sub: fator},
    ``renovaveis_nivel`` ("decomp" escala as usinas para bater com os equivalentes;
    "capacidade" usa só capacidade × fator de capacidade histórico), ``semente``.
    """
    op = opcoes or {}
    dias = dias_da_semana(x["inicio"])
    sorteio = dias_sorteados(f, x["inicio"], cenario, int(op.get("semente", 0)))

    # forma típica da carga e patamares (pela carga do SIN no dia típico)
    tipicas = {s: np.concatenate([f.perfil_carga(s, d.month, tipo_dia(d)) for d in dias]) for s in SUBMERCADOS}
    niveis_carga = {s: _por_patamar(carga, {"submercado": s}) for s in SUBMERCADOS}
    medias = {s: sum(niveis_carga[s].values()) / max(len(niveis_carga[s]), 1) for s in SUBMERCADOS}
    sin = sum(tipicas[s] * medias[s] for s in SUBMERCADOS)
    pat = patamares(sin, x["horas"])

    ajuste = op.get("ajuste_carga") or {}
    out_carga = {}
    for s in SUBMERCADOS:
        alvo = {p: v * float(ajuste.get(s, 1.0)) for p, v in niveis_carga[s].items()}
        serie = ajustar(tipicas[s], pat, alvo)
        if cenario > 0:
            razao = np.concatenate(
                [
                    _razao(h.get(f"carga_{s}") if h else None, _tipico(f, d.month, tipo_dia(d), f"carga_{s}"))
                    for d, h in zip(dias, sorteio, strict=True)
                ]
            )
            serie = serie * razao
        out_carga[s] = serie

    # MMGD (PQ marcado como MMGD), por submercado e fonte
    out_mmgd: dict[str, np.ndarray] = {}
    for s in SUBMERCADOS:
        total = np.zeros(7 * MEIAS)
        for fonte in sorted({r["fonte"] for r in pequenas if r.get("mmgd") and r["submercado"] == s}):
            alvo = _por_patamar(pequenas, {"submercado": s, "fonte": fonte, "mmgd": True})
            forma = np.concatenate([_perfil_ou_plano(f.perfil_mmgd, s, d.month, fonte) for d in dias])
            total += ajustar_total(forma, pat, alvo)
        out_mmgd[s] = total

    out_ande = ajustar(np.ones(7 * MEIAS), pat, _por_patamar(ande, {}))

    # renováveis por usina
    nivel = op.get("renovaveis_nivel", "decomp")
    out_ren: dict[str, np.ndarray] = {}
    grupos: dict[tuple[str, str], list[str]] = {}
    for u in usinas:
        fc = np.concatenate(
            [
                np.repeat(f.perfil_fc(d.month, usina_id=u["usina_id"], fonte=u["fonte_energia"], uf=u.get("uf")), 2)
                * _razao(
                    h.get(f"fc_{u['fonte_energia']}_{u['submercado']}") if h else None,
                    _tipico(f, d.month, None, f"fc_{u['fonte_energia']}_{u['submercado']}") if h else None,
                )
                for d, h in zip(dias, sorteio, strict=True)
            ]
        )
        out_ren[u["usina_id"]] = np.minimum(fc * (u["cap_mw"] or 0.0), u["cap_mw"] or 0.0)
        grupos.setdefault((u["submercado"], u["fonte_energia"]), []).append(u["usina_id"])
    fatores = {}
    if nivel == "decomp":
        for (s, fonte), ids in grupos.items():
            alvo = _por_patamar(renovaveis, {"submercado": s, "fonte": fonte})
            if not alvo:
                continue
            soma = sum(out_ren[i] for i in ids)
            alvo_ajustado = ajustar_total(soma, pat, alvo)
            with np.errstate(divide="ignore", invalid="ignore"):
                k = np.where(soma > 1e-9, alvo_ajustado / soma, 0.0)
            for i in ids:
                out_ren[i] = out_ren[i] * k
            fatores[f"{s}|{fonte}"] = float(alvo_ajustado.sum() / soma.sum()) if soma.sum() else None
    return {
        "dias": [d.isoformat() for d in dias],
        "patamar": pat,
        "carga": out_carga,
        "mmgd": out_mmgd,
        "ande": out_ande,
        "renovaveis": out_ren,
        "fator_renovaveis": fatores,
    }


def _perfil_ou_plano(fn, s: str, mes: int, fonte: str) -> np.ndarray:
    try:
        return fn(s, mes, fonte)
    except KeyError:
        return np.ones(MEIAS)
