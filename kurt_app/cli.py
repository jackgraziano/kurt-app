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

    c = sub.add_parser("cenario", help="cria cenários e importa expansão prevista")
    c.add_argument("acao", choices=["criar", "importar", "listar"])
    c.add_argument("nome", nargs="?")
    c.add_argument("arquivo", nargs="?", help="CSV de linhas ou usinas, ou JSON de eventos")
    c.add_argument("--cadastro", required=True)
    c.add_argument("--cenarios", help="pasta dos cenários (padrão: dados/cenarios/<cadastro>)")
    c.add_argument("--tipo", choices=["linhas", "usinas", "json"], default="json")
    c.add_argument("--pai")
    c.add_argument("--descricao", default="")
    c.add_argument("--horizonte")

    a = p.parse_args(argv)

    if a.cmd == "cenario":
        from pathlib import Path

        from .api import RAIZ
        from .cadastro import Cadastro
        from .cenario import Cenarios
        from .importar import eventos_de_linhas, eventos_de_usinas

        pasta = a.cenarios or RAIZ / "dados" / "cenarios" / Path(a.cadastro).resolve().name
        cens = Cenarios(Cadastro(a.cadastro), pasta)
        if a.acao == "listar":
            json.dump(cens.listar(), sys.stdout, ensure_ascii=False, indent=2)
            print()
            return 0
        if not a.nome:
            p.error("informe o nome do cenário")
        if a.acao == "criar":
            d = cens.criar(a.nome, a.descricao, a.pai, a.horizonte)
            print(f"cenário {d['nome']} criado (pai: {d['pai'] or 'base'}, horizonte {d['horizonte']})")
            return 0
        texto = Path(a.arquivo).read_text(encoding="utf-8")
        evs = (
            eventos_de_linhas(texto)
            if a.tipo == "linhas"
            else eventos_de_usinas(texto)
            if a.tipo == "usinas"
            else json.loads(texto)
        )
        prontos = cens.adicionar_eventos(a.nome, evs)
        print(f"{len(prontos)} evento(s) adicionados a {a.nome}")
        return 0

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
