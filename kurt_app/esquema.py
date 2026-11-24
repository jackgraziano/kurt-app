"""Esquema do cadastro do sistema físico.

O cadastro tem cinco tabelas, uma por arquivo Parquet: barras, ramos, usinas,
restricoes e eventos. As quatro primeiras descrevem o sistema e têm vigência;
a quinta registra o que mudou. A especificação completa está no documento
"Especificação: configuração do sistema físico do Kurt".

Convenção de vigência: o intervalo é semiaberto, [vigente_de, vigente_ate).
`vigente_ate` nulo quer dizer que o registro ainda vale. Uma entidade pode ter
várias versões (linhas com o mesmo id), sem sobreposição de intervalos.

Campos estruturados (termos, limites, faixas_lpp, campos) ficam como texto
JSON no Parquet, para que o esquema não mude quando a fórmula mudar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa


@dataclass(frozen=True)
class Tabela:
    nome: str
    chave: str  # coluna do id da entidade
    colunas: tuple[tuple[str, pa.DataType], ...]
    obrigatorias: tuple[str, ...]
    json: tuple[str, ...] = ()
    com_vigencia: bool = True

    @property
    def schema(self) -> pa.Schema:
        return pa.schema(list(self.colunas))

    @property
    def nomes(self) -> list[str]:
        return [c for c, _ in self.colunas]


VIGENCIA = (
    ("vigente_de", pa.date32()),
    ("vigente_ate", pa.date32()),
    ("fonte", pa.string()),
    ("confianca", pa.string()),
)

BARRAS = Tabela(
    nome="barras",
    chave="barra_id",
    colunas=(
        ("barra_id", pa.string()),
        ("num_pwf", pa.int64()),
        ("nome", pa.string()),
        ("kv", pa.float64()),
        ("area", pa.int64()),
        ("submercado", pa.string()),
        ("subestacao", pa.string()),
        ("uf", pa.string()),
        ("pos_x", pa.float64()),
        ("pos_y", pa.float64()),
        ("carga_leve_mw", pa.float64()),
        ("carga_media_mw", pa.float64()),
        ("carga_pesada_mw", pa.float64()),
        ("elevatoria", pa.bool_()),
        *VIGENCIA,
    ),
    obrigatorias=("barra_id", "nome", "kv", "subestacao", "vigente_de", "fonte", "confianca"),
)

RAMOS = Tabela(
    nome="ramos",
    chave="ramo_id",
    colunas=(
        ("ramo_id", pa.string()),
        ("de", pa.string()),
        ("para", pa.string()),
        ("circuito", pa.int64()),
        ("tipo", pa.string()),  # linha, trafo, csc, elo_cc
        ("nome", pa.string()),
        ("x_pct", pa.float64()),
        ("cap_normal_mva", pa.float64()),
        ("cap_emerg_mva", pa.float64()),
        ("estado_base", pa.string()),  # ligado, desligado
        *VIGENCIA,
    ),
    obrigatorias=("ramo_id", "de", "para", "tipo", "vigente_de", "fonte", "confianca"),
)

USINAS = Tabela(
    nome="usinas",
    chave="usina_id",
    colunas=(
        ("usina_id", pa.string()),
        ("cod_deck", pa.string()),
        ("nome", pa.string()),
        ("fonte_energia", pa.string()),  # EOL, UFV, MMGD, UHE, UTE, PCH
        ("barra_id", pa.string()),
        ("submercado", pa.string()),
        ("cap_mw", pa.float64()),
        ("flag_corte", pa.int64()),
        ("ids_ons", pa.string()),  # JSON: lista de id_ons / ceg
        ("ponto_conexao_ons", pa.string()),
        ("grupo_eixo", pa.string()),
        *VIGENCIA,
    ),
    obrigatorias=("usina_id", "nome", "fonte_energia", "barra_id", "vigente_de", "fonte", "confianca"),
    json=("ids_ons",),
)

RESTRICOES = Tabela(
    nome="restricoes",
    chave="restr_id",
    colunas=(
        ("restr_id", pa.string()),
        ("tipo", pa.string()),  # RESP, DREF, LPP, RE, IA
        ("num_deck", pa.int64()),
        ("nome", pa.string()),
        ("termos", pa.string()),  # JSON: [{elemento, id, coeficiente}]
        ("limites", pa.string()),  # JSON: [{de, ate, inferior, superior}]
        ("faixas_lpp", pa.string()),  # JSON
        ("violavel", pa.bool_()),
        ("penalidade", pa.float64()),
        ("io_ons", pa.string()),
        ("formula_texto", pa.string()),  # leitura humana da restrição
        *VIGENCIA,
    ),
    obrigatorias=("restr_id", "tipo", "nome", "vigente_de", "fonte", "confianca"),
    json=("termos", "limites", "faixas_lpp"),
)

EVENTOS = Tabela(
    nome="eventos",
    chave="evento_id",
    colunas=(
        ("evento_id", pa.string()),
        ("data", pa.date32()),
        ("acao", pa.string()),  # entra, sai, altera, renumera, reagrupa
        ("entidade", pa.string()),  # barras, ramos, usinas, restricoes
        ("entidade_id", pa.string()),
        ("campos", pa.string()),  # JSON: {campo: [antes, depois]}
        ("fonte", pa.string()),
        ("confianca", pa.string()),
        ("nota", pa.string()),
    ),
    obrigatorias=("evento_id", "data", "acao", "entidade", "entidade_id", "fonte", "confianca"),
    json=("campos",),
    com_vigencia=False,
)

TABELAS: dict[str, Tabela] = {t.nome: t for t in (BARRAS, RAMOS, USINAS, RESTRICOES, EVENTOS)}
ENTIDADES = ("barras", "ramos", "usinas", "restricoes")

ACOES = ("entra", "sai", "altera", "renumera", "reagrupa")
CONFIANCAS = ("realizado", "contratado", "previsto", "especulativo")
TIPOS_RAMO = ("linha", "trafo", "csc", "elo_cc")
FONTES_ENERGIA = ("EOL", "UFV", "MMGD", "UHE", "UTE", "PCH", "CGH", "BIO")
SUBMERCADOS = ("SE", "S", "NE", "N")
