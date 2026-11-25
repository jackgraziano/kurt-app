"""Cenários de simulação: o que uma rodada do Kurt vai usar, aba por aba.

Um cenário de simulação tem um horizonte (início e fim) e um conjunto de
insumos, um por aba do app: decks, rede elétrica, carga, renováveis, hidro,
valor da água, térmicas e execução.
Cada insumo guarda só a escolha feita para ele. A rede aponta para um caso de
rede (o caso base ou uma camada de eventos sobre ele, ver ``cenario.py``); os
decks, para uma fonte de decks DECOMP e o mapa semana -> deck (``decks.py``).
Os outros insumos são esqueletos e ficam "a definir" até cada aba ganhar sua
lógica.

Cada cenário é um JSON na pasta de simulações:

    {
      "nome": "nov26-expansao",
      "descricao": "Projeção de 12 meses com a expansão oficial",
      "inicio": "2026-12-01",
      "fim": "2027-11-30",
      "criado_em": "2026-11-24T18:40:00",
      "atualizado_em": "2026-11-24T18:40:00",
      "insumos": {"rede": {"caso": "expansao"}, "carga": {}, ...}
    }
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .cenario import BASE, NOME_VALIDO, CenarioErro, Cenarios

HORIZONTE_MAX_ANOS = 10


@dataclass(frozen=True)
class Insumo:
    id: str
    rotulo: str
    descricao: str
    disponivel: bool  # a aba já tem lógica; as outras são esqueleto


INSUMOS: tuple[Insumo, ...] = (
    Insumo(
        "decks",
        "Decks",
        "Decks DECOMP que dão o nível de cada semana do horizonte (carga, térmicas, renováveis, vazões, FCF): "
        "de uma pasta (ex.: estudo do Myria baixado) ou os oficiais do ONS. Mês sem deck repete o anterior.",
        True,
    ),
    Insumo(
        "rede",
        "Rede elétrica",
        "Barras, linhas, transformadores e usinas, com a expansão datada (caso de rede), "
        "e as restrições elétricas: RESP, LPP, RE e limites de intercâmbio.",
        True,
    ),
    Insumo("carga", "Carga", "Demanda por submercado e por barra, mês a mês e na curva horária.", False),
    Insumo(
        "renovaveis",
        "Renováveis",
        "Geração eólica e solar prevista (séries e cenários), usinas não despacháveis (flag 0), MMGD e limites de corte.",
        False,
    ),
    Insumo(
        "hidro",
        "Hidro",
        "Afluências, níveis iniciais dos reservatórios, FPHA e restrições hidráulicas "
        "(operuh, defluências mínimas, máximas e sazonais).",
        False,
    ),
    Insumo(
        "valor_agua",
        "Valor da água",
        "Função de custo futuro (cortes do DECOMP) que valora o armazenamento no fim de cada dia: "
        "de rodadas de planejamento do horizonte, do DECOMP oficial ou repetindo a do deck base. "
        "Sem ela o Kurt não roda.",
        False,
    ),
    Insumo("termicas", "Térmicas", "Disponibilidade, inflexibilidade, CVU e manutenções das térmicas.", False),
    Insumo(
        "execucao",
        "Execução",
        "Como rodar: dias (todos ou típicos), discretização, quantos cenários combinar entre carga, "
        "renováveis e afluência, saídas (P10, P50, P90 por mês, submercado, tipo de corte e usina) "
        "e opções do PL (rede, UC). Daqui a rodada será disparada.",
        False,
    ),
)
IDS_INSUMOS = tuple(i.id for i in INSUMOS)


class SimulacaoErro(CenarioErro):
    pass


def _data(s: Any, campo: str) -> date:
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        raise SimulacaoErro(f"{campo} inválido: {s!r} (use AAAA-MM-DD)") from None


def _mais_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def horizonte_padrao(data_base: str | None) -> tuple[str, str]:
    """Do mês seguinte ao caso base, 12 meses."""
    b = date.fromisoformat(data_base) if data_base else date.today()
    ini = _mais_meses(b.replace(day=1), 1)
    fim = date.fromordinal(_mais_meses(ini, 12).toordinal() - 1)
    return ini.isoformat(), fim.isoformat()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Simulacoes:
    """Cenários de simulação gravados numa pasta, um JSON por cenário."""

    def __init__(self, pasta: str | Path, casos_rede: Cenarios):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.casos_rede = casos_rede
        self._trava = threading.Lock()

    def _arq(self, nome: str) -> Path:
        if not NOME_VALIDO.match(nome or ""):
            raise SimulacaoErro("nome inválido: use letras minúsculas, números, - e _ (até 64)")
        return self.pasta / f"{nome}.json"

    def ler(self, nome: str) -> dict:
        arq = self._arq(nome)
        if not arq.exists():
            raise KeyError(nome)
        return json.loads(arq.read_text())

    def listar(self) -> list[dict]:
        out = [json.loads(a.read_text()) for a in self.pasta.glob("*.json")]
        return sorted(out, key=lambda s: s.get("atualizado_em", ""), reverse=True)

    def usam_caso(self, caso: str) -> list[str]:
        """Cenários de simulação que usam um caso de rede (para não apagá-lo por baixo deles)."""
        return [s["nome"] for s in self.listar() if s.get("insumos", {}).get("rede", {}).get("caso") == caso]

    def _validar(self, s: dict) -> None:
        ini = _data(s.get("inicio"), "início")
        fim = _data(s.get("fim"), "fim")
        if fim < ini:
            raise SimulacaoErro("o fim do horizonte vem antes do início")
        if (fim - ini).days > 366 * HORIZONTE_MAX_ANOS:
            raise SimulacaoErro(f"horizonte maior que {HORIZONTE_MAX_ANOS} anos")
        insumos = s.get("insumos", {})
        desconhecidos = set(insumos) - set(IDS_INSUMOS)
        if desconhecidos:
            raise SimulacaoErro(f"insumo desconhecido: {', '.join(sorted(desconhecidos))}")
        caso = insumos.get("rede", {}).get("caso", BASE)
        if caso != BASE and caso not in {c["nome"] for c in self.casos_rede.listar()}:
            raise SimulacaoErro(f"caso de rede {caso} não existe")

    def _gravar(self, s: dict) -> dict:
        self._validar(s)
        arq = self._arq(s["nome"])
        tmp = arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1))
        tmp.replace(arq)
        return s

    def criar(
        self, nome: str, descricao: str = "", inicio: str | None = None, fim: str | None = None, caso_rede: str = BASE
    ) -> dict:
        with self._trava:
            if self._arq(nome).exists():
                raise SimulacaoErro(f"já existe um cenário chamado {nome}")
            ini_p, fim_p = horizonte_padrao(self.casos_rede.base.data_base())
            agora = _agora()
            s = {
                "nome": nome,
                "descricao": descricao,
                "inicio": inicio or ini_p,
                "fim": fim or fim_p,
                "criado_em": agora,
                "atualizado_em": agora,
                "insumos": {i: {} for i in IDS_INSUMOS} | {"rede": {"caso": caso_rede or BASE}},
            }
            return self._gravar(s)

    def atualizar(self, nome: str, mudancas: dict) -> dict:
        """Muda descrição, horizonte ou a escolha de um ou mais insumos (por aba, substituindo a aba toda)."""
        with self._trava:
            s = self.ler(nome)
            for campo in ("descricao", "inicio", "fim"):
                if campo in mudancas:
                    s[campo] = mudancas[campo]
            for aba, valor in (mudancas.get("insumos") or {}).items():
                if not isinstance(valor, dict):
                    raise SimulacaoErro(f"insumo {aba}: esperado um objeto")
                s.setdefault("insumos", {})[aba] = valor
            s["atualizado_em"] = _agora()
            return self._gravar(s)

    def apagar(self, nome: str) -> None:
        """Apaga o cenário e os casos de rede que eram só dele (importados de JSON)."""
        with self._trava:
            arq = self._arq(nome)
            if not arq.exists():
                raise KeyError(nome)
            arq.unlink()
        for caso in self.casos_rede.listar():
            if caso.get("simulacao") == nome and not self.usam_caso(caso["nome"]):
                self.casos_rede.apagar(caso["nome"])
