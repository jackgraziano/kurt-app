"""API HTTP local (FastAPI). Serve também o front compilado, se existir."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .cadastro import Cadastro, CadastroErro
from .esquema import ENTIDADES
from .validar import relatorio, validar

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend" / "dist"


def criar_app(pasta_cadastro: str | Path | None = None) -> FastAPI:
    pasta = pasta_cadastro or os.environ.get("KURT_APP_CADASTRO") or RAIZ / "fixtures" / "acu3_20260910"
    cad = Cadastro(pasta)
    app = FastAPI(title="kurt-app", version=__version__)
    app.state.cadastro = cad

    @app.get("/api/meta")
    def meta():
        return {"versao_app": __version__, "pasta": str(cad.pasta), **cad.meta()}

    @app.get("/api/configuracao")
    def configuracao(
        data: str = Query(..., description="AAAA-MM-DD"),
        kv_min: float | None = None,
        submercado: str | None = None,
    ):
        _data_valida(data)
        return cad.configuracao(data, kv_min=kv_min, submercado=submercado)

    @app.get("/api/eventos")
    def eventos(
        de: str | None = None,
        ate: str | None = None,
        entidade: str | None = None,
        confianca: str | None = None,
    ):
        for d in (de, ate):
            if d:
                _data_valida(d)
        if entidade and entidade not in ENTIDADES:
            raise HTTPException(422, f"entidade deve ser uma de {ENTIDADES}")
        return cad.eventos(de, ate, entidade, confianca)

    @app.get("/api/entidade/{tipo}/{ident}")
    def entidade(tipo: str, ident: str):
        if tipo not in ENTIDADES:
            raise HTTPException(404, f"tipo desconhecido: {tipo}")
        try:
            return cad.entidade(tipo, ident)
        except KeyError:
            raise HTTPException(404, f"{tipo} {ident} não encontrada")

    @app.get("/api/diferenca")
    def diferenca(a: str, b: str):
        _data_valida(a)
        _data_valida(b)
        return cad.diferenca(a, b)

    @app.get("/api/validacao")
    def validacao():
        return relatorio(validar(cad))

    if FRONT.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONT / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(FRONT / "index.html")

    return app


def _data_valida(s: str) -> None:
    from datetime import date

    try:
        date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, f"data inválida: {s} (use AAAA-MM-DD)")


__all__ = ["criar_app", "CadastroErro"]
