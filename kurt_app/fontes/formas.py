"""Formas (perfis) históricas para dar forma horária ou semi-horária aos níveis do DECOMP.

Os Parquet são gerados por ``scripts/formas_historico.py`` a partir do datalake no NAS e
gravados em ``dados/formas/``. Este módulo só os lê (e não acessa o NAS):

- ``carga.parquet`` (submercado, mes, tipo_dia, meia_hora, fator): carga sem MMGD;
  ``fator`` é a carga da meia hora dividida pela média do mês. Num mês típico, a média
  ponderada pelos dias de cada tipo vale 1, de modo que fator × MWmed do DECOMP reproduz
  o nível do estágio e diferencia dia útil, sábado (e feriado) e domingo (e Natal/Ano-Novo).
- ``mmgd.parquet`` (submercado, fonte, mes, meia_hora, fc): perfil da MMGD com média 1 no
  mês (o histórico do ONS não traz a capacidade, só a geração).
- ``fc_usinas.parquet`` (id_ons, nome, ceg, fonte, uf, submercado, mes, hora, fc): fator de
  capacidade médio por usina ou conjunto do ONS, por mês e hora.
- ``fc_grupos.parquet`` (fonte, uf, mes, hora, fc): o mesmo por fonte e UF, ponderado pela
  capacidade (para usinas sem casamento); ``uf = "BR"`` é o país inteiro.
- ``mapa_usinas.parquet`` (usina_id, id_ons, via, score, cap_mw, uf_grupo, fonte, cap_ons_mw,
  cap_estimada_mw, cap_origem): usina do
  caso base -> usina ou conjunto do ONS cujo perfil ela usa (``via``: nome, ponto_conexao ou
  grupo_fonte_uf; no último, ``uf_grupo`` é a UF da barra ou, sem ela, a UF de maior
  capacidade da fonte no submercado). ``cap_ons_mw`` é a capacidade instalada do ONS da usina
  casada; ``cap_estimada_mw``, o máximo da previsão em decks DESSEM recentes; ``cap_origem`` diz
  qual vale (ons, decks_dessem ou caso).
- ``dias.parquet`` (data, mes, tipo_dia, carga_<sub>, fc_<fonte>_<sub>): dias históricos
  com as 48 meias horas normalizadas, para sortear dias inteiros (carga e renováveis
  juntas, preservando a correlação).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

TIPOS_DIA = ("util", "sabado", "domingo")
SUBMERCADOS = ("SE", "S", "NE", "N")


# ---------------------------------------------------------------- calendário


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes = (h + m - 7 * n + 114) // 31
    dia = (h + m - 7 * n + 114) % 31 + 1
    return date(ano, mes, dia)


@cache
def feriados(ano: int) -> frozenset[date]:
    """Feriados nacionais e os pontos facultativos que derrubam a carga (carnaval, Corpus Christi)."""
    p = pascoa(ano)
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))  # Consciência Negra, nacional desde 2024
    moveis = [p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)]
    return frozenset([date(ano, m, d) for m, d in fixos] + moveis)


# Natal e Ano-Novo ficam abaixo de um domingo; os demais feriados ficam entre o sábado e o domingo, mais perto
# do sábado (carga verificada do SE, 2024-2026: Consciência Negra 2025 em 0,64 numa escala domingo = 0, sábado = 1)
FERIADOS_DOMINGO = ((1, 1), (12, 25))


def tipo_dia(d: date) -> str:
    """util, sabado ou domingo; feriado em dia útil conta como sábado, salvo Natal e Ano-Novo (domingo)."""
    if d.weekday() == 6 or (d.month, d.day) in FERIADOS_DOMINGO:
        return "domingo"
    if d.weekday() == 5 or d in feriados(d.year):
        return "sabado"
    return "util"


def datas(mes: str, tipo: str | None = None) -> list[date]:
    """Dias do mês AAAA-MM, opcionalmente só de um tipo (util, sabado ou domingo)."""
    a, m = (int(x) for x in mes.split("-"))
    d = date(a, m, 1)
    out = []
    while d.month == m:
        if tipo is None or tipo_dia(d) == tipo:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- leitura dos perfis


class Formas:
    """Perfis lidos de uma pasta (padrão: dados/formas do kurt-app)."""

    def __init__(self, pasta: str | Path):
        self.pasta = Path(pasta)
        self._t: dict[str, pa.Table] = {}

    def tabela(self, nome: str) -> pa.Table:
        if nome not in self._t:
            arq = self.pasta / f"{nome}.parquet"
            if not arq.exists():
                raise FileNotFoundError(f"{arq} não existe: rode scripts/formas_historico.py")
            self._t[nome] = pq.read_table(arq)
        return self._t[nome]

    def _vetor(self, nome: str, filtros: dict, coluna_x: str, coluna_v: str, n: int) -> np.ndarray:
        t = self.tabela(nome)
        m = None
        for k, v in filtros.items():
            c = pc.equal(t[k], v)
            m = c if m is None else pc.and_(m, c)
        t = t.filter(m) if m is not None else t
        if t.num_rows == 0:
            raise KeyError(f"{nome}: sem perfil para {filtros}")
        x = t[coluna_x].to_numpy()
        out = np.full(n, np.nan)
        out[x] = t[coluna_v].to_numpy()
        return out

    def perfil_carga(self, submercado: str, mes: int, tipo: str) -> np.ndarray:
        """48 fatores (meia hora 0 = 00:00-00:30) da carga sem MMGD."""
        return self._vetor("carga", {"submercado": submercado, "mes": mes, "tipo_dia": tipo}, "meia_hora", "fator", 48)

    def perfil_mmgd(self, submercado: str, mes: int, fonte: str = "UFV") -> np.ndarray:
        """48 fatores com média 1 no dia típico do mês."""
        return self._vetor("mmgd", {"submercado": submercado, "fonte": fonte, "mes": mes}, "meia_hora", "fc", 48)

    def perfil_fc(
        self, mes: int, usina_id: str | None = None, fonte: str | None = None, uf: str | None = None
    ) -> np.ndarray:
        """24 fatores de capacidade horários: da usina do ONS casada com `usina_id`, senão do grupo fonte × UF."""
        if usina_id is not None:
            mapa = self.tabela("mapa_usinas")
            sel = mapa.filter(pc.equal(mapa["usina_id"], usina_id))
            if sel.num_rows:
                r = sel.slice(0, 1).to_pylist()[0]
                if r.get("id_ons"):
                    try:
                        return self._vetor("fc_usinas", {"id_ons": r["id_ons"], "mes": mes}, "hora", "fc", 24)
                    except KeyError:
                        pass
                fonte = fonte or r.get("fonte")
                uf = uf or r.get("uf_grupo")
        if fonte is None:
            raise KeyError("perfil_fc: informe usina_id ou fonte (e uf)")
        # UF sem histórico da fonte naquele mês: cai para o país ("BR")
        for u in [uf, "BR"] if uf else ["BR"]:
            try:
                return self._vetor("fc_grupos", {"fonte": fonte, "uf": u, "mes": mes}, "hora", "fc", 24)
            except KeyError:
                continue
        raise KeyError(f"fc_grupos: sem perfil de {fonte} no mês {mes}")

    def capacidade(self, usina_id: str) -> tuple[float, str] | None:
        """Capacidade da usina (MW) e a origem: "ons" (instalada, da usina casada), "decks_dessem"
        (máximo da previsão em decks recentes) ou None se o mapa não tiver nenhuma das duas."""
        mapa = self.tabela("mapa_usinas")
        if "cap_origem" not in mapa.column_names:
            return None
        sel = mapa.filter(pc.equal(mapa["usina_id"], usina_id))
        if not sel.num_rows:
            return None
        r = sel.slice(0, 1).to_pylist()[0]
        if r.get("cap_ons_mw"):
            return float(r["cap_ons_mw"]), "ons"
        if r.get("cap_estimada_mw"):
            return float(r["cap_estimada_mw"]), "decks_dessem"
        return None

    def dias(self, mes: int | None = None, tipo: str | None = None) -> pa.Table:
        """Dias históricos (para sorteio conjunto), filtrados por mês do ano e tipo de dia."""
        t = self.tabela("dias")
        if mes is not None:
            t = t.filter(pc.equal(t["mes"], mes))
        if tipo is not None:
            t = t.filter(pc.equal(t["tipo_dia"], tipo))
        return t


def nivelar(perfil: np.ndarray, media_alvo: float) -> np.ndarray:
    """Escala um perfil para que a média dê `media_alvo` (por exemplo, o MWmed do estágio)."""
    m = float(np.nanmean(perfil))
    return perfil * (media_alvo / m) if m else np.zeros_like(perfil)
