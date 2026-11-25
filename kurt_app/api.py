"""API HTTP local (FastAPI). Serve também o front compilado, se existir.

Todas as consultas aceitam ?cenario=<nome> (padrão: "base", o caso base sem
eventos). Os cenários ficam em arquivos JSON na pasta de cenários.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, decks
from .cadastro import Cadastro, CadastroErro
from .cenario import BASE, CenarioErro, Cenarios
from .esquema import ENTIDADES, TABELAS
from .importar import eventos_de_linhas, eventos_de_usinas
from .insumo_json import exportar as exportar_insumo
from .insumo_json import importar as importar_insumo
from .layout import CENTRO_UF, contornos_uf
from .simulacao import INSUMOS, SimulacaoErro, Simulacoes
from .validar import relatorio, validar

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend" / "dist"


@contextmanager
def _erros(nao_existe: str, prefixo: str = "") -> Iterator[None]:
    """Traduz os erros do domínio em respostas HTTP: inexistente → 404, inválido → 422."""
    try:
        yield
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, nao_existe) from None
    except (CenarioErro, ValueError) as e:
        raise HTTPException(422, f"{prefixo}{e}") from None


def criar_app(
    pasta_cadastro: str | Path | None = None,
    pasta_cenarios: str | Path | None = None,
    pasta_simulacoes: str | Path | None = None,
) -> FastAPI:
    pasta = pasta_cadastro or os.environ.get("KURT_APP_CADASTRO") or RAIZ / "fixtures" / "acu3_20260910"
    base = Cadastro(pasta)
    cen_dir = (
        pasta_cenarios
        or os.environ.get("KURT_APP_CENARIOS")
        or RAIZ / "dados" / "cenarios" / Path(pasta).resolve().name
    )
    cenarios = Cenarios(base, cen_dir)
    sim_dir = (
        pasta_simulacoes
        or os.environ.get("KURT_APP_SIMULACOES")
        or RAIZ / "dados" / "simulacoes" / Path(pasta).resolve().name
    )
    simulacoes = Simulacoes(sim_dir, cenarios)
    app = FastAPI(title="kurt-app", version=__version__)
    app.state.cenarios = cenarios

    def cad(nome: str | None) -> Cadastro:
        with _erros(f"cenário {nome} não existe", f"cenário {nome} não se aplica: "):
            return cenarios.cadastro(nome or BASE)

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
        c = cad(cenario)
        with _erros(f"{tipo} {ident} não encontrada"):
            return c.entidade(tipo, ident)

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
        with _erros(f"cenário pai {dados.get('pai')} não existe"):
            return cenarios.criar(
                dados.get("nome", ""), dados.get("descricao", ""), dados.get("pai"), dados.get("horizonte")
            )

    @app.get("/api/cenarios/{nome}")
    def ler(nome: str):
        with _erros(f"cenário {nome} não existe"):
            cadeia = cenarios.cadeia(nome)
        proprio = cadeia[-1]
        herdados = [dict(e, cenario=c["nome"]) for c in cadeia[:-1] for e in c.get("eventos", [])]
        return {**proprio, "herdados": herdados, "cadeia": [c["nome"] for c in cadeia]}

    @app.delete("/api/cenarios/{nome}", status_code=204)
    def apagar(nome: str):
        usam = simulacoes.usam_caso(nome)
        if usam:
            raise HTTPException(422, f"o caso de rede {nome} é usado pelos cenários {', '.join(usam)}")
        with _erros(f"cenário {nome} não existe"):
            cenarios.apagar(nome)

    @app.post("/api/cenarios/{nome}/eventos", status_code=201)
    def adicionar(nome: str, eventos: list[dict[str, Any]] | dict[str, Any] = Body(...)):
        lista = eventos if isinstance(eventos, list) else [eventos]
        with _erros(f"cenário {nome} não existe"):
            return cenarios.adicionar_eventos(nome, lista)

    @app.delete("/api/cenarios/{nome}/eventos/{evento_id}", status_code=204)
    def remover(nome: str, evento_id: str):
        with _erros(f"evento {evento_id} não existe em {nome}", "não dá para remover: "):
            cenarios.remover_evento(nome, evento_id)

    @app.post("/api/cenarios/{nome}/importar", status_code=201)
    def importar(
        nome: str, tipo: str = Query(..., pattern="^(linhas|usinas)$"), texto: str = Body(..., media_type="text/plain")
    ):
        with _erros(f"cenário {nome} não existe"):
            evs = eventos_de_linhas(texto) if tipo == "linhas" else eventos_de_usinas(texto)
            return cenarios.adicionar_eventos(nome, evs)

    # -- cenários de simulação (tela principal e abas de insumos) ------------
    @app.get("/api/insumos")
    def insumos():
        return [asdict(i) for i in INSUMOS]

    @app.get("/api/simulacoes")
    def listar_simulacoes():
        return simulacoes.listar()

    @app.post("/api/simulacoes", status_code=201)
    def criar_simulacao(dados: dict[str, Any] = Body(...)):
        """Cria o cenário; com "decks": {"fonte": {...}}, já monta o mapa de decks (e não cria se falhar)."""
        with _erros(""):
            s = simulacoes.criar(
                dados.get("nome", ""),
                dados.get("descricao", ""),
                dados.get("inicio"),
                dados.get("fim"),
                dados.get("caso_rede") or BASE,
            )
            if dados.get("decks"):
                try:
                    s = simulacoes.atualizar(s["nome"], {"insumos": {"decks": decks.montar(s, dados["decks"])}})
                except (SimulacaoErro, ValueError):
                    simulacoes.apagar(s["nome"])
                    raise
            return s

    # -- decks DECOMP -----------------------------------------------------------
    def fontes_decks() -> list[dict]:
        """Fontes configuradas na máquina: KURT_APP_DECKS (pastas, separadas por ':') e KURT_APP_DECKS_ONS."""
        out = [
            {"tipo": "pasta", "caminho": p, "rotulo": Path(p).name}
            for p in os.environ.get("KURT_APP_DECKS", "").split(os.pathsep)
            if p and Path(p).is_dir()
        ]
        ons = os.environ.get("KURT_APP_DECKS_ONS")
        if ons and Path(ons).is_dir():
            out.append({"tipo": "ons", "caminho": ons, "rotulo": "ONS oficial (NAS)"})
        return out

    @app.get("/api/decks/fontes")
    def listar_fontes_decks():
        return fontes_decks()

    @app.get("/api/decks")
    def listar_decks(tipo: str, caminho: str, inicio: str, fim: str):
        """Decks DECOMP de uma fonte e o mapa semana -> deck que o app faria para o horizonte."""
        with _erros(f"fonte {caminho} não existe"):
            ini, f = date.fromisoformat(inicio), date.fromisoformat(fim)
            lista = decks.catalogo({"tipo": tipo, "caminho": caminho}, ini, f)
            return {
                "decks": [d.dict() for d in lista],
                "semanas": decks.mapear(decks.semanas_operativas(ini, f), lista),
            }

    @app.get("/api/simulacoes/{nome}")
    def ler_simulacao(nome: str):
        with _erros(f"cenário {nome} não existe"):
            return simulacoes.ler(nome)

    @app.patch("/api/simulacoes/{nome}")
    def atualizar_simulacao(nome: str, mudancas: dict[str, Any] = Body(...)):
        with _erros(f"cenário {nome} não existe"):
            return simulacoes.atualizar(nome, mudancas)

    @app.get("/api/simulacoes/{nome}/insumos/{aba}")
    def baixar_insumo(nome: str, aba: str, baixar: bool = False):
        """JSON da aba, no envelope de insumo_json. Com ?baixar=1, vem como anexo."""
        with _erros(f"cenário {nome} ou aba {aba} não existe"):
            doc = exportar_insumo(simulacoes, nome, aba)
        cab = {"Content-Disposition": f'attachment; filename="{nome}-{aba}.json"'} if baixar else {}
        return JSONResponse(doc, headers=cab)

    @app.post("/api/simulacoes/{nome}/insumos/{aba}")
    def subir_insumo(nome: str, aba: str, doc: dict[str, Any] = Body(...)):
        with _erros(f"cenário {nome} ou aba {aba} não existe"):
            return importar_insumo(simulacoes, nome, aba, doc)

    @app.delete("/api/simulacoes/{nome}", status_code=204)
    def apagar_simulacao(nome: str):
        with _erros(f"cenário {nome} não existe"):
            simulacoes.apagar(nome)

    @app.get("/api/ufs")
    def ufs():
        """Contorno simplificado das UFs e o centro usado no layout, para o fundo do mapa."""
        return {
            uf: {"aneis": [a.tolist() for a in aneis], "centro": CENTRO_UF.get(uf)}
            for uf, aneis in contornos_uf().items()
        }

    @app.get("/api/esquema")
    def esquema():
        return {
            n: {"chave": t.chave, "colunas": t.nomes, "obrigatorias": list(t.obrigatorias)} for n, t in TABELAS.items()
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
        raise HTTPException(422, f"data inválida: {s} (use AAAA-MM-DD)") from None


__all__ = ["CadastroErro", "criar_app"]
