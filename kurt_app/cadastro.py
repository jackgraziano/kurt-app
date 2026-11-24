"""Leitura e consultas do cadastro (Parquet lidos com DuckDB).

Um cadastro é uma pasta com barras.parquet, ramos.parquet, usinas.parquet,
restricoes.parquet e eventos.parquet. Qualquer data é um recorte: as versões
vigentes em [vigente_de, vigente_ate).
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .esquema import ENTIDADES, TABELAS, Tabela


class CadastroErro(Exception):
    pass


def _ler_tabela(pasta: Path, tabela: Tabela) -> pa.Table:
    arq = pasta / f"{tabela.nome}.parquet"
    if not arq.exists():
        if tabela.nome == "eventos":
            return tabela.schema.empty_table()
        raise CadastroErro(f"falta {arq}")
    t = pq.read_table(arq)
    faltam = [c for c in tabela.obrigatorias if c not in t.column_names]
    if faltam:
        raise CadastroErro(f"{arq.name}: faltam as colunas {faltam}")
    # completa colunas opcionais ausentes com nulos, na ordem do esquema
    cols = []
    for nome, tipo in tabela.colunas:
        if nome in t.column_names:
            cols.append(t.column(nome).cast(tipo))
        else:
            cols.append(pa.nulls(t.num_rows, type=tipo))
    return pa.Table.from_arrays(cols, schema=tabela.schema)


def _linhas(cur: duckdb.DuckDBPyConnection, tabela: Tabela) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        for c in tabela.json:
            if d.get(c) is not None:
                try:
                    d[c] = json.loads(d[c])
                except (TypeError, ValueError):
                    pass
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
        out.append(d)
    return out


class Cadastro:
    """Consultas sobre um cadastro carregado em memória."""

    def __init__(
        self,
        pasta: str | Path | None = None,
        tabelas: dict[str, pa.Table] | None = None,
        info: dict[str, Any] | None = None,
    ):
        """Carrega de uma pasta de Parquet ou de tabelas já em memória (cenários)."""
        if tabelas is None:
            if pasta is None:
                raise CadastroErro("informe a pasta ou as tabelas do cadastro")
            self.pasta = Path(pasta)
            if not self.pasta.is_dir():
                raise CadastroErro(f"pasta do cadastro não existe: {self.pasta}")
            tabelas = {nome: _ler_tabela(self.pasta, t) for nome, t in TABELAS.items()}
            arq = self.pasta / "cadastro.json"
            if info is None:
                info = json.loads(arq.read_text()) if arq.exists() else {}
        else:
            self.pasta = Path(pasta) if pasta else Path(".")
            tabelas = {
                nome: tabelas.get(nome, t.schema.empty_table()).cast(t.schema)
                for nome, t in TABELAS.items()
            }
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self.tabelas: dict[str, pa.Table] = tabelas
        for nome, t in tabelas.items():
            self._con.register(nome, t)
        self.info: dict[str, Any] = info or {}

    def data_base(self) -> str | None:
        """Maior vigente_de com fonte 'deck': a data da foto do caso base."""
        with self._lock:
            r = self._q("SELECT max(vigente_de) FROM barras WHERE fonte = 'deck'").fetchone()
        return r[0].isoformat() if r and r[0] else None

    # -- auxiliares -------------------------------------------------------
    def _q(self, sql: str, params: list | None = None) -> duckdb.DuckDBPyConnection:
        return self._con.execute(sql, params or [])

    def _sel(self, tabela: Tabela, sql: str, params: list | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return _linhas(self._q(sql, params), tabela)

    @staticmethod
    def _vig(alias: str = "") -> str:
        p = f"{alias}." if alias else ""
        return f"({p}vigente_de <= ?::DATE AND ({p}vigente_ate IS NULL OR {p}vigente_ate > ?::DATE))"

    # -- consultas --------------------------------------------------------
    def meta(self) -> dict[str, Any]:
        """Período coberto, meses com mudança e contagens."""
        with self._lock:
            datas = []
            for nome in ENTIDADES:
                r = self._q(
                    f"SELECT min(vigente_de), max(vigente_de), max(vigente_ate) FROM {nome}"
                ).fetchone()
                datas.extend(d for d in r if d is not None)
            r = self._q("SELECT min(data), max(data) FROM eventos").fetchone()
            datas.extend(d for d in r if d is not None)
            meses = self._q(
                """
                SELECT strftime(data, '%Y-%m') AS mes, count(*) AS n,
                       count(*) FILTER (WHERE confianca = 'realizado') AS n_realizado
                FROM eventos GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
            contagens = {n: self.tabelas[n].num_rows for n in TABELAS}
        if self.info.get("horizonte"):
            datas.append(date.fromisoformat(self.info["horizonte"]))
        return {
            "inicio": min(datas).isoformat() if datas else None,
            "fim": max(datas).isoformat() if datas else None,
            "data_base": self.data_base(),
            "eventos_por_mes": [
                {"mes": m, "n": n, "n_realizado": nr} for m, n, nr in meses
            ],
            "linhas": contagens,
            "info": self.info,
        }

    def configuracao(
        self,
        data: str,
        kv_min: float | None = None,
        submercado: str | None = None,
    ) -> dict[str, Any]:
        """Recorte do sistema vigente na data."""
        pb: list[Any] = [data, data]
        filtro_b = ""
        if kv_min is not None:
            filtro_b += " AND kv >= ?"
            pb.append(kv_min)
        if submercado:
            filtro_b += " AND submercado = ?"
            pb.append(submercado)
        barras = self._sel(
            TABELAS["barras"],
            f"SELECT * FROM barras WHERE {self._vig()}{filtro_b} ORDER BY barra_id",
            pb,
        )
        ids = {b["barra_id"] for b in barras}
        ramos = [
            r
            for r in self._sel(
                TABELAS["ramos"],
                f"SELECT * FROM ramos WHERE {self._vig()} ORDER BY ramo_id",
                [data, data],
            )
            if r["de"] in ids and r["para"] in ids
        ]
        usinas = [
            u
            for u in self._sel(
                TABELAS["usinas"],
                f"SELECT * FROM usinas WHERE {self._vig()} ORDER BY usina_id",
                [data, data],
            )
            if u["barra_id"] in ids
        ]
        restricoes = self._sel(
            TABELAS["restricoes"],
            f"SELECT * FROM restricoes WHERE {self._vig()} ORDER BY restr_id",
            [data, data],
        )
        return {
            "data": data,
            "barras": barras,
            "ramos": ramos,
            "usinas": usinas,
            "restricoes": restricoes,
        }

    def eventos(
        self,
        de: str | None = None,
        ate: str | None = None,
        entidade: str | None = None,
        confianca: str | None = None,
    ) -> list[dict[str, Any]]:
        """Eventos com data em [de, ate], mais recentes primeiro."""
        cond, p = ["TRUE"], []
        if de:
            cond.append("data >= ?::DATE")
            p.append(de)
        if ate:
            cond.append("data <= ?::DATE")
            p.append(ate)
        if entidade:
            cond.append("entidade = ?")
            p.append(entidade)
        if confianca:
            cond.append("confianca = ?")
            p.append(confianca)
        return self._sel(
            TABELAS["eventos"],
            f"SELECT * FROM eventos WHERE {' AND '.join(cond)} ORDER BY data DESC, evento_id",
            p,
        )

    def entidade(self, tipo: str, ident: str) -> dict[str, Any]:
        """Todas as versões de uma entidade e os eventos que a citam."""
        if tipo not in ENTIDADES:
            raise CadastroErro(f"tipo desconhecido: {tipo}")
        t = TABELAS[tipo]
        versoes = self._sel(
            t, f"SELECT * FROM {tipo} WHERE {t.chave} = ? ORDER BY vigente_de", [ident]
        )
        if not versoes:
            raise KeyError(ident)
        evs = self._sel(
            TABELAS["eventos"],
            "SELECT * FROM eventos WHERE entidade = ? AND entidade_id = ? ORDER BY data",
            [tipo, ident],
        )
        rel: dict[str, Any] = {}
        if tipo == "barras":
            rel["ramos"] = sorted(
                {r["ramo_id"] for r in self._sel(
                    TABELAS["ramos"], "SELECT * FROM ramos WHERE de = ? OR para = ?", [ident, ident]
                )}
            )
            rel["usinas"] = sorted(
                {u["usina_id"] for u in self._sel(
                    TABELAS["usinas"], "SELECT * FROM usinas WHERE barra_id = ?", [ident]
                )}
            )
        return {"tipo": tipo, "id": ident, "versoes": versoes, "eventos": evs, "relacionados": rel}

    def diferenca(self, a: str, b: str) -> dict[str, Any]:
        """O que entrou, saiu ou mudou entre as datas a e b."""
        ca, cb = self.configuracao(a), self.configuracao(b)
        ignorar = {"vigente_de", "vigente_ate", "fonte", "confianca"}
        out: dict[str, Any] = {"a": a, "b": b}
        for tipo in ENTIDADES:
            chave = TABELAS[tipo].chave
            ia = {x[chave]: x for x in ca[tipo]}
            ib = {x[chave]: x for x in cb[tipo]}
            alterados = []
            for k in sorted(ia.keys() & ib.keys()):
                campos = {
                    c: [ia[k].get(c), ib[k].get(c)]
                    for c in ia[k]
                    if c not in ignorar and ia[k].get(c) != ib[k].get(c)
                }
                if campos:
                    alterados.append({"id": k, "campos": campos})
            out[tipo] = {
                "entraram": sorted(ib.keys() - ia.keys()),
                "sairam": sorted(ia.keys() - ib.keys()),
                "alterados": alterados,
            }
        return out
