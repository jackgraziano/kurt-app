"""Posição indicativa das subestações no mapa.

Não usa coordenadas reais (decisão de 24/11/2026): cada subestação parte do
centro aproximado da sua UF, e um layout de molas afasta as subestações e
aproxima as que têm ramo entre si. O resultado é calculado uma vez, com todas
as barras do período, para que nada salte de lugar quando o mês muda.

As coordenadas ficam em graus aproximados (x = longitude, y = latitude), só
para manter a forma do país reconhecível.
"""

from __future__ import annotations

import zlib

import numpy as np
import pyarrow as pa

# centro aproximado de cada UF (longitude, latitude); indicativo
CENTRO_UF: dict[str, tuple[float, float]] = {
    "AC": (-70.5, -9.0), "AL": (-36.6, -9.6), "AP": (-51.8, 1.4), "AM": (-64.6, -4.2),
    "BA": (-41.7, -12.6), "CE": (-39.6, -5.1), "DF": (-47.9, -15.8), "ES": (-40.3, -19.6),
    "GO": (-49.6, -16.0), "MA": (-45.3, -5.0), "MT": (-55.9, -12.6), "MS": (-54.6, -20.5),
    "MG": (-44.6, -18.5), "PA": (-52.3, -4.0), "PB": (-36.8, -7.2), "PR": (-51.6, -24.6),
    "PE": (-37.9, -8.4), "PI": (-42.7, -7.7), "RJ": (-42.9, -22.4), "RN": (-36.5, -5.8),
    "RS": (-53.2, -29.8), "RO": (-63.0, -10.9), "RR": (-61.4, 2.1), "SC": (-50.5, -27.2),
    "SP": (-48.5, -22.2), "SE": (-37.4, -10.6), "TO": (-48.3, -10.2),
}
CENTRO_PADRAO = (-47.9, -15.8)


def _semente(nome: str) -> float:
    return (zlib.crc32(nome.encode()) % 10_000) / 10_000.0


def posicoes(
    subestacoes: dict[str, str | None],
    arestas: list[tuple[str, str]],
    iteracoes: int | None = None,
    raio_uf: float = 0.9,
) -> dict[str, tuple[float, float]]:
    """Posição de cada subestação.

    subestacoes: nome -> UF (ou None). arestas: pares de subestações ligadas.
    """
    nomes = sorted(subestacoes)
    n = len(nomes)
    if n == 0:
        return {}
    if iteracoes is None:
        iteracoes = 400 if n <= 1000 else 200
    idx = {s: i for i, s in enumerate(nomes)}
    ancora = np.array([CENTRO_UF.get(subestacoes[s] or "", CENTRO_PADRAO) for s in nomes], dtype=float)
    # ponto inicial determinístico em volta do centro da UF
    ang = np.array([2 * np.pi * _semente(s) for s in nomes])
    rad = np.array([raio_uf * (0.3 + 0.7 * _semente(s[::-1])) for s in nomes])
    pos = ancora + np.c_[np.cos(ang) * rad, np.sin(ang) * rad]

    ar = np.array(
        [(idx[a], idx[b]) for a, b in arestas if a in idx and b in idx and a != b], dtype=int
    ).reshape(-1, 2)
    dist_ideal = 0.45
    passo = 0.08
    for it in range(iteracoes):
        desloc = np.zeros_like(pos)
        # repulsão só entre vizinhos próximos: grade com células do tamanho do corte,
        # cada célula contra as 3×3 ao redor (O(n) em vez de O(n²))
        corte = 4 * dist_ideal
        cel = np.floor(pos / corte).astype(np.int64)
        chave = cel[:, 0] * 100_003 + cel[:, 1]
        ordem = np.argsort(chave, kind="stable")
        ch_ord = chave[ordem]
        unicas, inicio = np.unique(ch_ord, return_index=True)
        fim = np.append(inicio[1:], len(ch_ord))
        faixa = {int(u): (int(a), int(b)) for u, a, b in zip(unicas, inicio, fim)}
        for u, (a, b) in faixa.items():
            cx, cy = divmod(u, 100_003) if u >= 0 else (u // 100_003, u % 100_003)
            meus = ordem[a:b]
            viz = [ordem[slice(*faixa[k])] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   if (k := (cx + dx) * 100_003 + (cy + dy)) in faixa]
            outros = np.concatenate(viz)
            d = pos[meus, None, :] - pos[None, outros, :]
            dist2 = (d**2).sum(-1) + 1e-6
            dist2[meus[:, None] == outros[None, :]] = np.inf
            fator = dist_ideal**2 / dist2 / np.sqrt(dist2)
            fator[dist2 > corte**2] = 0.0
            desloc[meus] += (fator[:, :, None] * d).sum(1)
        # atração pelas arestas
        if len(ar):
            v = pos[ar[:, 1]] - pos[ar[:, 0]]
            dist = np.sqrt((v**2).sum(-1)) + 1e-9
            f = (dist / dist_ideal)[:, None] * v / dist[:, None] * 0.5
            np.add.at(desloc, ar[:, 0], f)
            np.add.at(desloc, ar[:, 1], -f)
        # âncora fraca na UF
        desloc += (ancora - pos) * 0.15
        norma = np.sqrt((desloc**2).sum(-1, keepdims=True)) + 1e-9
        t = passo * (1 - it / iteracoes) + 0.005
        pos += desloc / norma * np.minimum(norma, t)
    return {s: (round(float(pos[i, 0]), 4), round(float(pos[i, 1]), 4)) for s, i in idx.items()}


def aplicar_posicoes(barras: pa.Table, ramos: pa.Table) -> pa.Table:
    """Preenche pos_x e pos_y das barras pela posição da sua subestação."""
    b = barras.to_pylist()
    sub_uf: dict[str, str | None] = {}
    for r in b:
        sub_uf.setdefault(r["subestacao"], r.get("uf"))
    barra_sub = {r["barra_id"]: r["subestacao"] for r in b}
    arestas = sorted(
        {
            (barra_sub[r["de"]], barra_sub[r["para"]])
            for r in ramos.to_pylist()
            if r["de"] in barra_sub and r["para"] in barra_sub
        }
    )
    pos = posicoes(sub_uf, arestas)
    xs = [pos[r["subestacao"]][0] for r in b]
    ys = [pos[r["subestacao"]][1] for r in b]
    i_x = barras.schema.get_field_index("pos_x")
    i_y = barras.schema.get_field_index("pos_y")
    out = barras.set_column(i_x, "pos_x", pa.array(xs, pa.float64()))
    return out.set_column(i_y, "pos_y", pa.array(ys, pa.float64()))
