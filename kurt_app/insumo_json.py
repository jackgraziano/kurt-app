"""JSON de cada aba de insumo: baixar, editar à mão e subir de volta.

Diretriz do projeto: toda aba de insumo tem por trás um documento JSON com tudo
o que ela decide. O usuário pode baixá-lo, mudar o que quiser fora do app e
subir de volta; o app valida e grava. É a porta para qualquer ajuste manual
que a interface ainda não oferece.

Envelope comum a todas as abas:

    {
      "formato": "kurt-app/insumo",
      "versao": 1,
      "insumo": "rede",              # id da aba
      "cenario": "dez26-expansao",   # informativo; ao subir, vale o cenário da URL
      "horizonte": {"inicio": "...", "fim": "..."},
      "exportado_em": "...",
      "_ajuda": ["..."],             # instruções; ignorado ao subir
      "dados": { ... }               # o conteúdo da aba
    }

Cada aba registra um par exportar/importar em ``ABAS``. Sem registro, a aba usa
o genérico: ``dados`` é o objeto guardado em ``insumos[aba]`` do cenário.

Rede elétrica: ``dados`` traz a lista completa de eventos do caso de rede
(camadas achatadas, do caso base para cima). Ao subir, os eventos viram um caso
de rede próprio do cenário (``<cenário>-rede``), validado contra o caso base,
e o cenário passa a usá-lo. Casos de rede compartilhados nunca são alterados.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from . import decks
from .cenario import BASE, CenarioErro
from .simulacao import IDS_INSUMOS, SimulacaoErro, Simulacoes

FORMATO = "kurt-app/insumo"
VERSAO = 1

Exportar = Callable[[Simulacoes, dict], tuple[dict, list[str]]]
Importar = Callable[[Simulacoes, dict, dict], dict]


# -- genérico ---------------------------------------------------------------
def _exportar_generico(aba: str) -> Exportar:
    def f(_sims: Simulacoes, s: dict) -> tuple[dict, list[str]]:
        return s.get("insumos", {}).get(aba, {}), [
            "Edite o objeto 'dados' e suba o arquivo de volta na mesma aba.",
            "Esta aba ainda não tem lógica no app: o conteúdo é guardado como está.",
        ]

    return f


def _importar_generico(aba: str) -> Importar:
    def f(sims: Simulacoes, s: dict, dados: dict) -> dict:
        return sims.atualizar(s["nome"], {"insumos": {aba: dados}})

    return f


# -- rede elétrica ------------------------------------------------------------
AJUDA_REDE = [
    "'eventos' é a lista completa de mudanças sobre o caso base, em qualquer ordem (o app ordena por data).",
    "Ao subir, os eventos viram o caso de rede '<cenário>-rede', só deste cenário; os outros casos não mudam.",
    "Cada evento: data (AAAA-MM-DD), acao (entra | sai | altera), entidade (barras | ramos | usinas | restricoes),"
    " entidade_id, e opcionalmente fonte, confianca (realizado | contratado | previsto | especulativo) e nota.",
    "entra: 'registro' com os campos da entidade (ex.: de, para, tipo, x_pct, cap_normal_mva para ramos;"
    " nome, fonte_energia, barra_id, cap_mw para usinas). Sem entidade_id, o id é gerado.",
    "altera: 'campos' com os valores novos, ex.: {\"cap_normal_mva\": 2400}. Vale a partir da data.",
    "sai: a entidade deixa de existir na data.",
    "Ids de barras, ramos e usinas aparecem na Ficha do mapa. Para retirar um evento, apague-o da lista.",
    "'camada' diz de qual caso de rede o evento veio; é só informativo.",
    'Exemplo: {"data": "2027-06-01", "acao": "altera", "entidade": "ramos",'
    ' "entidade_id": "R5350-5428-1", "campos": {"cap_normal_mva": 2400}}',
]


def nome_caso_proprio(simulacao: str) -> str:
    return f"{simulacao[:59]}-rede"


def _exportar_rede(sims: Simulacoes, s: dict) -> tuple[dict, list[str]]:
    casos = sims.casos_rede
    caso = s.get("insumos", {}).get("rede", {}).get("caso", BASE)
    cadeia = casos.cadeia(caso) if caso != BASE else []
    eventos = [dict(e, camada=c["nome"]) for c in cadeia for e in c.get("eventos", [])]
    base = casos.base
    return {
        "caso_de_origem": caso,
        "caso_base": {"nome": base.info.get("nome"), "deck": base.info.get("deck"), "data_base": base.data_base()},
        "eventos": eventos,
    }, AJUDA_REDE


def _importar_rede(sims: Simulacoes, s: dict, dados: dict) -> dict:
    eventos = dados.get("eventos")
    if not isinstance(eventos, list) or not all(isinstance(e, dict) for e in eventos):
        raise SimulacaoErro("rede: 'eventos' deve ser uma lista de objetos")
    nome = s["nome"]
    outros = [x for x in sims.usam_caso(nome_caso_proprio(nome)) if x != nome]
    if outros:
        raise SimulacaoErro(f"o caso {nome_caso_proprio(nome)} também é usado por {', '.join(outros)}")
    limpos = [{k: v for k, v in e.items() if k != "camada"} for e in eventos]
    origem = dados.get("caso_de_origem") or "?"
    try:
        sims.casos_rede.substituir(
            nome_caso_proprio(nome),
            limpos,
            descricao=f"Rede do cenário {nome}, importada de JSON (origem: {origem})",
            horizonte=s["fim"],
            dono=nome,
        )
    except CenarioErro as e:
        raise SimulacaoErro(f"rede: {e}") from None
    return sims.atualizar(nome, {"insumos": {"rede": {"caso": nome_caso_proprio(nome)}}})


# -- decks --------------------------------------------------------------------
def _exportar_decks(_sims: Simulacoes, s: dict) -> tuple[dict, list[str]]:
    insumo = dict(s.get("insumos", {}).get("decks") or {})
    insumo.setdefault("fonte", None)
    insumo.setdefault("so_rv0", True)
    insumo.setdefault("semanas", [])
    if insumo["fonte"]:
        try:
            ini, fim = date.fromisoformat(s["inicio"]), date.fromisoformat(s["fim"])
            insumo["decks_disponiveis"] = [d.dict() for d in decks.catalogo(insumo["fonte"], ini, fim)]
        except SimulacaoErro as e:
            insumo["decks_disponiveis"] = []
            insumo["erro_fonte"] = str(e)
    insumo["avisos"] = decks.avisos(insumo, s)
    return insumo, decks.AJUDA_DECKS


def _importar_decks(sims: Simulacoes, s: dict, dados: dict) -> dict:
    return sims.atualizar(s["nome"], {"insumos": {"decks": decks.montar(s, dados)}})


ABAS: dict[str, tuple[Exportar, Importar]] = {
    "rede": (_exportar_rede, _importar_rede),
    "decks": (_exportar_decks, _importar_decks),
}


def _par(aba: str) -> tuple[Exportar, Importar]:
    if aba not in IDS_INSUMOS:
        raise KeyError(aba)
    return ABAS.get(aba, (_exportar_generico(aba), _importar_generico(aba)))


def exportar(sims: Simulacoes, nome: str, aba: str) -> dict:
    s = sims.ler(nome)
    dados, ajuda = _par(aba)[0](sims, s)
    return {
        "formato": FORMATO,
        "versao": VERSAO,
        "insumo": aba,
        "cenario": nome,
        "horizonte": {"inicio": s["inicio"], "fim": s["fim"]},
        "exportado_em": datetime.now().isoformat(timespec="seconds"),
        "_ajuda": ajuda,
        "dados": dados,
    }


def importar(sims: Simulacoes, nome: str, aba: str, doc: Any) -> dict:
    """Valida o envelope e entrega 'dados' à aba. Devolve o cenário atualizado."""
    importar_aba = _par(aba)[1]
    s = sims.ler(nome)
    if not isinstance(doc, dict) or doc.get("formato") != FORMATO:
        raise SimulacaoErro(f"arquivo não é um JSON de insumo do kurt-app (formato {FORMATO!r})")
    if doc.get("versao") != VERSAO:
        raise SimulacaoErro(f"versão {doc.get('versao')!r} não suportada (esperada {VERSAO})")
    if doc.get("insumo") != aba:
        raise SimulacaoErro(f"este JSON é da aba {doc.get('insumo')!r}, não de {aba!r}")
    if not isinstance(doc.get("dados"), dict):
        raise SimulacaoErro("'dados' deve ser um objeto")
    return importar_aba(sims, s, doc["dados"])
