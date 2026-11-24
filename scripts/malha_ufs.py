"""Gera kurt_app/ufs.json: contorno simplificado de cada UF, para o fundo do mapa.

Entrada: GeoJSON dos estados com a sigla em `sigla` (ou `SIGLA`), por exemplo
o brazil-states.geojson do projeto click_that_hood, derivado da malha do IBGE.

    python scripts/malha_ufs.py brazil-states.geojson [--tolerancia 0.03]

Saída: {"AC": [[[lon, lat], ...], ...], ...}, uma lista de anéis externos por UF,
simplificados (Douglas-Peucker) e com 2 casas decimais (~1 km), ~100 KB.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

SAIDA = Path(__file__).resolve().parent.parent / "kurt_app" / "ufs.json"


def simplificar(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker iterativo."""
    n = len(pts)
    if n < 4:
        return pts
    manter = np.zeros(n, dtype=bool)
    manter[0] = manter[-1] = True
    pilha = [(0, n - 1)]
    while pilha:
        i, j = pilha.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1 : j]
        d = b - a
        L = np.hypot(*d)
        dist = np.hypot(*(seg - a).T) if L == 0 else np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            manter[m] = True
            pilha += [(i, m), (m, j)]
    return pts[manter]


def area(anel: np.ndarray) -> float:
    x, y = anel[:, 0], anel[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--tolerancia", type=float, default=0.03)
    ap.add_argument("--area-min", type=float, default=0.02, help="descarta ilhas menores (graus²)")
    args = ap.parse_args()

    dados = json.loads(Path(args.geojson).read_text())
    saida: dict[str, list] = {}
    for f in dados["features"]:
        p = f["properties"]
        uf = p.get("sigla") or p.get("SIGLA")
        g = f["geometry"]
        polis = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        aneis = []
        for poli in polis:
            ext = np.asarray(poli[0], dtype=float)[:, :2]
            if area(ext) < args.area_min:
                continue
            s = simplificar(ext, args.tolerancia)
            if len(s) >= 4:
                aneis.append(np.round(s, 2).tolist())
        saida[uf] = aneis
    SAIDA.write_text(json.dumps(saida, separators=(",", ":")))
    print(
        f"{SAIDA}: {len(saida)} UFs, {sum(len(a) for v in saida.values() for a in v)} vértices, "
        f"{SAIDA.stat().st_size // 1024} KB"
    )


if __name__ == "__main__":
    main()
