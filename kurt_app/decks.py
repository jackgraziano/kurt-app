"""Decks DECOMP que compõem um cenário de simulação (aba Decks).

O DECOMP dá o nível de cada insumo (carga, térmicas, renováveis, vazões, FCF)
semana a semana; o histórico dá a forma horária (ver ``fontes/formas.py``).
Esta aba decide qual deck responde por cada semana operativa do horizonte.

Semana operativa: de sábado a sexta. Ela pertence ao mês da sua sexta-feira
(a RV0 de dezembro/2026 começa no sábado 28/11/2026). Um deck RVn do mês M
começa na semana n do mês e traz um estágio por semana até o fim do mês.

Fontes de decks:

- ``pasta``: uma pasta com um deck por subpasta ou por .zip (ex.: os decks de
  um estudo do Myria já baixados). A data de início vem do registro DT do
  dadger e a revisão, da extensão ``dadger.rvN``;
- ``ons``: os decks oficiais do ONS no NAS
  (``<raiz>/<ano>/<AAAAMMDD>__DEC_ONS_<MMAAAA>_RV<n>_VE.zip``), lidos pelo nome,
  úteis para backtest.

Mês sem deck repete o deck mais recente antes dele (marcado ``repetido``),
e a aba avisa.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from .simulacao import SimulacaoErro

SABADO = 5  # date.weekday()
RE_DADGER = re.compile(r"(?:^|/)dadger\.rv(\d+)$", re.IGNORECASE)
RE_ONS = re.compile(r"^(\d{8})__DEC_ONS_(\d{2})(\d{4})_RV(\d+)_VE\.zip$", re.IGNORECASE)


# -- semanas operativas --------------------------------------------------------
def sabado_ate(d: date) -> date:
    """O sábado em ``d`` ou antes dele."""
    return d - timedelta(days=(d.weekday() - SABADO) % 7)


def mes_operativo(inicio_semana: date) -> str:
    """AAAA-MM do mês a que a semana pertence (o da sexta-feira)."""
    sexta = inicio_semana + timedelta(days=6)
    return f"{sexta.year:04d}-{sexta.month:02d}"


def inicio_mes_operativo(mes: str) -> date:
    a, m = map(int, mes.split("-"))
    return sabado_ate(date(a, m, 1))


def semanas_operativas(inicio: date, fim: date) -> list[dict]:
    """Semanas (sábado a sexta) que tocam [inicio, fim], com o mês e o índice no mês (1, 2, ...)."""
    out = []
    s = sabado_ate(inicio)
    while s <= fim:
        mes = mes_operativo(s)
        indice = (s - inicio_mes_operativo(mes)).days // 7 + 1
        out.append({"inicio": s.isoformat(), "fim": (s + timedelta(days=6)).isoformat(), "mes": mes, "semana": indice})
        s += timedelta(days=7)
    return out


# -- catálogo de decks ---------------------------------------------------------
@dataclass(frozen=True)
class Deck:
    id: str  # estável dentro da fonte: nome da subpasta ou do zip
    fonte: str  # pasta | ons
    caminho: str  # pasta ou zip do deck
    inicio: str  # sábado do primeiro estágio, AAAA-MM-DD
    mes: str  # mês operativo, AAAA-MM
    revisao: int

    def dict(self) -> dict:
        return asdict(self)


def _ler_dt(texto: str) -> date | None:
    """Registro DT do dadger: 'DT  dd mm aaaa' (data de início do estudo)."""
    for linha in texto.splitlines():
        if linha.startswith("DT"):
            partes = linha[2:].split()
            try:
                d, m, a = (int(x) for x in partes[:3])
                return date(a, m, d)
            except ValueError:
                return None
    return None


def _deck_da_pasta(item: Path) -> Deck | None:
    if item.is_dir():
        nomes = [p.name for p in item.iterdir()]
        dadger = next((n for n in nomes if RE_DADGER.search(n)), None)
        if not dadger:
            return None
        texto = (item / dadger).read_text(encoding="latin-1", errors="replace")
    elif item.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(item) as z:
                dadger = next((n for n in z.namelist() if RE_DADGER.search(n)), None)
                if not dadger:
                    return None
                texto = z.read(dadger).decode("latin-1", errors="replace")
        except zipfile.BadZipFile:
            return None
    else:
        return None
    rv = int(RE_DADGER.search(dadger).group(1))
    ini = _ler_dt(texto)
    if ini is None:
        return None
    return Deck(item.stem if item.is_file() else item.name, "pasta", str(item), ini.isoformat(), mes_operativo(ini), rv)


def listar_pasta(pasta: str | Path) -> list[Deck]:
    """Decks de uma pasta: uma subpasta ou um .zip por deck, com dadger.rvN dentro."""
    raiz = Path(pasta)
    if not raiz.is_dir():
        raise SimulacaoErro(f"pasta de decks não encontrada: {pasta}")
    decks = [d for item in sorted(raiz.iterdir()) if (d := _deck_da_pasta(item))]
    return sorted(decks, key=lambda d: (d.inicio, d.revisao))


def listar_ons(raiz: str | Path, inicio: date, fim: date) -> list[Deck]:
    """Decks oficiais do ONS cujo mês operativo cai em [inicio, fim], lidos só pelo nome do zip."""
    raiz = Path(raiz)
    if not raiz.is_dir():
        raise SimulacaoErro(f"pasta dos decks do ONS não encontrada: {raiz}")
    m_ini, m_fim = mes_operativo(sabado_ate(inicio)), mes_operativo(sabado_ate(fim))
    decks = []
    for ano in range(inicio.year - 1, fim.year + 1):
        pasta = raiz / str(ano)
        if not pasta.is_dir():
            continue
        for z in pasta.iterdir():
            m = RE_ONS.match(z.name)
            if not m:
                continue
            ini = date(int(m[1][:4]), int(m[1][4:6]), int(m[1][6:]))
            mes = f"{m[3]}-{m[2]}"
            if m_ini <= mes <= m_fim:
                decks.append(Deck(z.stem, "ons", str(z), ini.isoformat(), mes, int(m[4])))
    return sorted(decks, key=lambda d: (d.inicio, d.revisao))


# -- mapa semana -> deck -------------------------------------------------------
def mapear(semanas: list[dict], decks: list[Deck], so_rv0: bool = True) -> list[dict]:
    """Escolhe o deck e o estágio de cada semana.

    Para a semana k do mês M, vale o deck de M com a maior revisão <= k-1
    (com ``so_rv0``, só a RV0, como numa projeção). O estágio é k - revisão.
    Sem deck do mês, repete o deck mais recente de um mês anterior, no
    estágio de mesmo índice (limitado às semanas daquele mês).
    """
    usaveis = [d for d in decks if not so_rv0 or d.revisao == 0]
    por_mes: dict[str, list[Deck]] = {}
    for d in usaveis:
        por_mes.setdefault(d.mes, []).append(d)
    meses = sorted(por_mes)
    out = []
    for s in semanas:
        k = s["semana"]
        candidatos = [d for d in por_mes.get(s["mes"], []) if d.revisao <= k - 1]
        repetido = False
        if candidatos:
            d = max(candidatos, key=lambda d: d.revisao)
            estagio = k - d.revisao
        else:
            anteriores = [m for m in meses if m < s["mes"]] or meses[:1]
            if not anteriores:
                out.append(dict(s, deck=None, estagio=None, repetido=False))
                continue
            mes = anteriores[-1]
            d = min(por_mes[mes], key=lambda d: d.revisao)
            n_semanas = len(semanas_operativas_do_mes(mes))
            estagio = min(k, n_semanas) - d.revisao
            repetido = True
        out.append(dict(s, deck=d.id, estagio=estagio, repetido=repetido))
    return out


def semanas_operativas_do_mes(mes: str) -> list[dict]:
    a, m = map(int, mes.split("-"))
    prox = date(a + m // 12, m % 12 + 1, 1)
    ini = inicio_mes_operativo(mes)
    fim = inicio_mes_operativo(f"{prox.year:04d}-{prox.month:02d}") - timedelta(days=1)
    return semanas_operativas(ini, fim)


# -- insumo da aba ---------------------------------------------------------------
AJUDA_DECKS = [
    '\'fonte\' diz de onde vêm os decks: {"tipo": "pasta", "caminho": "/pasta/com/decks"} (uma subpasta'
    ' ou .zip por deck, com dadger.rvN) ou {"tipo": "ons", "caminho": "<raiz dos decks oficiais>"}.',
    "'so_rv0': true usa só a RV0 de cada mês, como numa projeção; false usa a revisão mais recente de cada semana.",
    "'semanas' lista as semanas operativas (sábado a sexta) do horizonte e o deck e o estágio de cada uma."
    " Para forçar outro deck numa semana, troque 'deck' (o id aparece em 'decks_disponiveis') e 'estagio'.",
    "Apague 'semanas' para o app refazer o mapa sozinho a partir da fonte.",
    "'repetido': true marca semanas sem deck do próprio mês, que repetem um deck anterior.",
    "'decks_disponiveis', 'caminhos' e 'avisos' são só informativos; são ignorados na subida.",
]


def catalogo(fonte: dict, inicio: date, fim: date) -> list[Deck]:
    if not isinstance(fonte, dict) or fonte.get("tipo") not in ("pasta", "ons") or not fonte.get("caminho"):
        raise SimulacaoErro('decks: \'fonte\' deve ser {"tipo": "pasta" | "ons", "caminho": "..."}')
    if fonte["tipo"] == "pasta":
        return listar_pasta(fonte["caminho"])
    return listar_ons(fonte["caminho"], inicio - timedelta(days=62), fim)


def _validar_semanas(semanas: list, esperadas: list[dict], ids: set[str]) -> list[dict]:
    if not isinstance(semanas, list) or not all(isinstance(x, dict) for x in semanas):
        raise SimulacaoErro("decks: 'semanas' deve ser uma lista de objetos")
    por_inicio = {x.get("inicio"): x for x in semanas}
    out = []
    for e in esperadas:
        x = por_inicio.get(e["inicio"])
        if x is None:
            raise SimulacaoErro(f"decks: falta a semana que começa em {e['inicio']}")
        if x.get("deck") not in ids:
            raise SimulacaoErro(f"decks: semana {e['inicio']}: deck {x.get('deck')!r} não está na fonte")
        est = x.get("estagio")
        if not isinstance(est, int) or not 1 <= est <= 6:
            raise SimulacaoErro(f"decks: semana {e['inicio']}: estágio {est!r} inválido (use 1 a 6)")
        out.append(dict(e, deck=x["deck"], estagio=est, repetido=bool(x.get("repetido", False))))
    return out


def montar(s: dict, dados: dict) -> dict:
    """Valida a escolha de decks e completa o mapa semana -> deck. Devolve o insumo a gravar."""
    ini, fim = date.fromisoformat(s["inicio"]), date.fromisoformat(s["fim"])
    fonte = dados.get("fonte")
    decks = catalogo(fonte, ini, fim)
    if not decks:
        raise SimulacaoErro(f"decks: nenhum deck DECOMP encontrado em {fonte['caminho']}")
    so_rv0 = bool(dados.get("so_rv0", True))
    esperadas = semanas_operativas(ini, fim)
    if dados.get("semanas"):
        semanas = _validar_semanas(dados["semanas"], esperadas, {d.id for d in decks})
    else:
        semanas = mapear(esperadas, decks, so_rv0)
    usados = {x["deck"] for x in semanas}
    return {
        "fonte": {"tipo": fonte["tipo"], "caminho": fonte["caminho"]},
        "so_rv0": so_rv0,
        "semanas": semanas,
        "caminhos": {d.id: d.caminho for d in decks if d.id in usados},
    }


def avisos(insumo: dict, s: dict) -> list[str]:
    """O que o usuário precisa saber sobre a escolha atual (sem decks, semanas repetidas, horizonte mudou)."""
    if not insumo.get("fonte"):
        return ["Nenhuma fonte de decks escolhida: as outras abas não têm de onde tirar os níveis."]
    out = []
    ini, fim = date.fromisoformat(s["inicio"]), date.fromisoformat(s["fim"])
    esperadas = [e["inicio"] for e in semanas_operativas(ini, fim)]
    if [x["inicio"] for x in insumo.get("semanas", [])] != esperadas:
        out.append("O horizonte mudou depois da escolha dos decks: salve a aba de novo para refazer o mapa.")
    rep = sorted({x["mes"] for x in insumo.get("semanas", []) if x.get("repetido")})
    if rep:
        meses = ", ".join(f"{m[5:]}/{m[:4]}" for m in rep)
        out.append(f"Sem deck do próprio mês em {meses}: essas semanas repetem um deck anterior.")
    return out
