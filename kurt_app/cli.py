"""Linha de comando: kurt-app servir | validar | layout."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kurt-app")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("servir", help="sobe a API e o front")
    s.add_argument("--cadastro", help="pasta do cadastro (padrão: KURT_APP_CADASTRO ou a fixture)")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para abrir pela rede local")
    s.add_argument("--porta", type=int, default=8000)

    v = sub.add_parser("validar", help="roda as checagens do cadastro")
    v.add_argument("cadastro")

    lay = sub.add_parser("layout", help="recalcula pos_x e pos_y das barras")
    lay.add_argument("cadastro")

    a = p.parse_args(argv)

    if a.cmd == "servir":
        import uvicorn

        from .api import criar_app

        uvicorn.run(criar_app(a.cadastro), host=a.host, port=a.porta)
        return 0

    if a.cmd == "validar":
        from .cadastro import Cadastro
        from .validar import relatorio, validar

        rel = relatorio(validar(Cadastro(a.cadastro)))
        json.dump(rel, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 1 if rel["erros"] else 0

    if a.cmd == "layout":
        from pathlib import Path

        import pyarrow.parquet as pq

        from .layout import aplicar_posicoes

        pasta = Path(a.cadastro)
        b = pq.read_table(pasta / "barras.parquet")
        r = pq.read_table(pasta / "ramos.parquet")
        pq.write_table(aplicar_posicoes(b, r), pasta / "barras.parquet")
        print(f"posições recalculadas para {b.num_rows} barras")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
