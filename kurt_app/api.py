"""API HTTP local (FastAPI). Serve também o front compilado, se existir.

Todas as consultas aceitam ?cenario=<nome> (padrão: "base", o caso base sem
eventos). Os cenários ficam em arquivos JSON na pasta de cenários.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .cadastro import Cadastro, CadastroErro
from .cenario import BASE, CenarioErro, Cenarios
from .esquema import ENTIDADES, TABELAS
from .validar import relatorio, validar

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend" / "dist"


def criar_app(pasta_cadastro: str | Path | None = None, pasta_cenarios: str | Path | None = None) -> FastAPI:
    pasta = pasta_cadastro or os.environ.get("KURT_APP_CADASTRO") or RAIZ / "fixtures" / "acu3_20260910"
    base = Cadastro(pasta)
    cen_dir = (
        pasta_cenarios
        or os.environ.get("KURT_APP_CENARIOS")
        or RAIZ / "dados" / "cenarios" / Path(pasta).resolve().name
    )
    cenarios = Cenarios(base, cen_dir)
    app = FastAPI(title="kurt-app", version=__version__)
    app.state.cenarios = cenarios

    def cad(nome: str | None) -> Cadastro:
        try:
            return cenarios.cadastro(nome or BASE)
        except KeyError:
            raise HTTPException(404, f"cenário {nome} não existe")
        except CenarioErro as e:
            raise HTTPException(422, f"cenário {nome} não se aplica: {e}")

    # -- consultas ----------------------------------------------------------
    @app.get("/api/meta")
    def meta(cenario: str | None = None):
        c = cad(cenario)
        return {"versao_app": __version__, "pasta": str(base.pasta), "cenario": cenario or BASE, **c.meta()}

    @app.get("/api/configuracao")
    def configuracao(
        data: str = Query(..., description="AAAA-MM-DD"),
        kv_min: float | None = None,
        submercado: str | None = None,
        cenario: str | None = None,
    ):
        _data_valida(data)
        return cad(cenario).configuracao(data, kv_min=kv_min, submercado=submercado)

    @app.get("/api/eventos")
    def eventos(
        de: str | None = None,
        ate: str | None = None,
        entidade: str | None = None,
        confianca: str | None = None,
        cenario: str | None = None,
    ):
        for d in (de, ate):
            if d:
                _data_valida(d)
        if entidade and entidade not in ENTIDADES:
            raise HTTPException(422, f"entidade deve ser uma de {ENTIDADES}")
        return cad(cenario).eventos(de, ate, entidade, confianca)

    @app.get("/api/entidade/{tipo}/{ident}")
    def entidade(tipo: str, ident: str, cenario: str | None = None):
        if tipo not in ENTIDADES:
            raise HTTPException(404, f"tipo desconhecido: {tipo}")
        try:
            return cad(cenario).entidade(tipo, ident)
        except KeyError:
            raise HTTPException(404, f"{tipo} {ident} não encontrada")

    @app.get("/api/diferenca")
    def diferenca(a: str, b: str, cenario: str | None = None):
        _data_valida(a)
        _data_valida(b)
        return cad(cenario).diferenca(a, b)

    @app.get("/api/validacao")
    def validacao(cenario: str | None = None):
        return relatorio(validar(cad(cenario)))

    @app.get("/api/barras")
    def barras(data: str, q: str = "", limite: int = 30, cenario: str | None = None):
        """Busca de barras vigentes por nome, número ou subestação (para os formulários)."""
        _data_valida(data)
        termo = q.strip().lower()
        out = []
        for b in cad(cenario).configuracao(data)["barras"]:
            texto = f"{b['barra_id']} {b['nome']} {b.get('num_pwf') or ''} {b['subestacao']}".lower()
            if not termo or termo in texto:
                out.append({k: b[k] for k in ("barra_id", "nome", "kv", "subestacao", "uf", "submercado")})
                if len(out) >= limite:
                    break
        return out

    # -- cenários -----------------------------------------------------------
    @app.get("/api/cenarios")
    def listar():
        return cenarios.listar()

    @app.post("/api/cenarios", status_code=201)
    def criar(dados: dict[str, Any] = Body(...)):
        try:
            return cenarios.criar(
                dados.get("nome", ""), dados.get("descricao", ""), dados.get("pai"), dados.get("horizonte")
            )
        except KeyError as e:
            raise HTTPException(404, f"cenário pai {e} não existe")
        except CenarioErro as e:
            raise HTTPException(422, str(e))

    @app.get("/api/cenarios/{nome}")
    def ler(nome: str):
        try:
            cadeia = cenarios.cadeia(nome)
        except KeyError:
            raise HTTPException(404, f"cenário {nome} não existe")
        except CenarioErro as e:
            raise HTTPException(422, str(e))
        proprio = cadeia[-1]
        herdados = [dict(e, cenario=c["nome"]) for c in cadeia[:-1] for e in c.get("eventos", [])]
        return {**proprio, "herdados": herdados, "cadeia": [c["nome"] for c in cadeia]}

    @app.delete("/api/cenarios/{nome}", status_code=204)
    def apagar(nome: str):
        try:
            cenarios.apagar(nome)
        except FileNotFoundError:
            raise HTTPException(404, f"cenário {nome} não existe")
        except CenarioErro as e:
            raise HTTPException(422, str(e))

    @app.post("/api/cenarios/{nome}/eventos", status_code=201)
    def adicionar(nome: str, eventos: list[dict[str, Any]] | dict[str, Any] = Body(...)):
        lista = eventos if isinstance(eventos, list) else [eventos]
        try:
            return cenarios.adicionar_eventos(nome, lista)
        except KeyError:
            raise HTTPException(404, f"cenário {nome} não existe")
        except CenarioErro as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/cenarios/{nome}/eventos/{evento_id}", status_code=204)
    def remover(nome: str, evento_id: str):
        try:
            cenarios.remover_evento(nome, evento_id)
        except KeyError:
            raise HTTPException(404, f"evento {evento_id} não existe em {nome}")
        except CenarioErro as e:
            raise HTTPException(422, f"não dá para remover: {e}")

    @app.post("/api/cenarios/{nome}/importar", status_code=201)
    def importar(nome: str, tipo: str = Query(..., pattern="^(linhas|usinas)$"), texto: str = Body(..., media_type="text/plain")):
        from .importar import eventos_de_linhas, eventos_de_usinas

        try:
            evs = eventos_de_linhas(texto) if tipo == "linhas" else eventos_de_usinas(texto)
            return cenarios.adicionar_eventos(nome, evs)
        except KeyError:
            raise HTTPException(404, f"cenário {nome} não existe")
        except (CenarioErro, ValueError) as e:
            raise HTTPException(422, str(e))

    @app.get("/api/esquema")
    def esquema():
        return {
            n: {"chave": t.chave, "colunas": t.nomes, "obrigatorias": list(t.obrigatorias)}
            for n, t in TABELAS.items()
        }

    if FRONT.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONT / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(FRONT / "index.html")

    return app


def _data_valida(s: str) -> None:
    try:
        date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, f"data inválida: {s} (use AAAA-MM-DD)")


__all__ = ["criar_app", "CadastroErro"]
