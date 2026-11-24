"""Cenários: o caso base mais camadas de eventos com data.

Um cenário é um arquivo JSON numa pasta de cenários:

    {
      "nome": "expansao-parpel",
      "descricao": "Obras e usinas previstas no PAR/PEL",
      "pai": null,                 # outro cenário por baixo deste, ou null (caso base)
      "horizonte": "2031-12-31",   # até onde a linha do tempo vai
      "eventos": [ ... ]
    }

Cada evento muda o sistema a partir da sua data:

- ``entra``: cria uma entidade. ``registro`` traz os campos (o id pode faltar,
  e então é gerado). A vigência começa na data do evento.
- ``sai``: fecha a versão vigente na data (``vigente_ate`` = data).
- ``altera``: fecha a versão vigente e abre outra a partir da data, com os
  ``campos`` novos ({campo: valor_novo}).

O caso base nunca é alterado: o cenário é composto em memória, aplicando os
eventos do pai e depois os próprios, em ordem de data. Assim dá para ter uma
camada de expansão oficial e, por cima dela, sensibilidades do usuário (por
exemplo, "e se a LT X atrasar seis meses").
"""

from __future__ import annotations

import copy
import json
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa

from .cadastro import Cadastro
from .esquema import ACOES, CONFIANCAS, ENTIDADES, TABELAS
from .layout import CENTRO_UF

NOME_VALIDO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
BASE = "base"
CAMPOS_PROTEGIDOS = {"vigente_de", "vigente_ate", "fonte", "confianca"}


class CenarioErro(ValueError):
    pass


def _d(s: Any) -> date:
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        raise CenarioErro(f"data inválida: {s!r} (use AAAA-MM-DD)") from None


def _vigente(r: dict, d: date) -> bool:
    return r["vigente_de"] <= d and (r["vigente_ate"] is None or r["vigente_ate"] > d)


def _jsonavel(v: Any) -> Any:
    return v.isoformat() if isinstance(v, date) else v


def _id_automatico(entidade: str, reg: dict, existentes: set[str]) -> str:
    if entidade == "ramos":
        a, b = sorted((str(reg.get("de", "")), str(reg.get("para", ""))))
        raiz = f"R{a.lstrip('B')}-{b.lstrip('B')}-N"
    elif entidade == "usinas":
        raiz = f"{reg.get('fonte_energia', 'U')}-N"
    elif entidade == "barras":
        raiz = "BN"
    else:
        raiz = f"{reg.get('tipo', 'R')}-N"
    k = 1
    while f"{raiz}{k}" in existentes:
        k += 1
    return f"{raiz}{k}"


def aplicar(base: Cadastro, eventos: list[dict]) -> tuple[dict[str, pa.Table], list[dict]]:
    """Aplica eventos sobre o caso base e devolve as tabelas compostas.

    Devolve também os eventos normalizados (com ids e campos antes/depois),
    no formato da tabela de eventos. Levanta CenarioErro se um evento não se
    aplica (entidade inexistente, versão não vigente, campo desconhecido).
    """
    linhas: dict[str, list[dict]] = {n: base.tabelas[n].to_pylist() for n in ENTIDADES}
    saida_eventos = base.tabelas["eventos"].to_pylist()

    ordenados = sorted(enumerate(eventos), key=lambda ie: (_d(ie[1]["data"]), ie[0]))
    for _, ev in ordenados:
        ent = ev.get("entidade")
        acao = ev.get("acao")
        if ent not in ENTIDADES:
            raise CenarioErro(f"{ev.get('evento_id')}: entidade inválida {ent!r}")
        if acao not in ("entra", "sai", "altera"):
            raise CenarioErro(f"{ev.get('evento_id')}: ação {acao!r} não suportada em cenários")
        conf = ev.get("confianca", "previsto")
        if conf not in CONFIANCAS:
            raise CenarioErro(f"{ev.get('evento_id')}: confiança inválida {conf!r}")
        tab = TABELAS[ent]
        chave = tab.chave
        d = _d(ev["data"])
        fonte = ev.get("fonte", "usuario")
        rows = linhas[ent]
        campos_antes_depois: dict[str, list] | None = None

        if acao == "entra":
            reg = dict(ev.get("registro") or {})
            fora = set(reg) - set(tab.nomes)
            if fora:
                raise CenarioErro(f"{ev.get('evento_id')}: campos fora do esquema {sorted(fora)}")
            ident = reg.get(chave) or ev.get("entidade_id")
            if not ident:
                ident = _id_automatico(ent, reg, {r[chave] for r in rows})
            if any(r[chave] == ident and _vigente(r, d) for r in rows):
                raise CenarioErro(f"{ev.get('evento_id')}: {ent} {ident} já existe em {d}")
            novo = {c: None for c in tab.nomes}
            novo.update(reg)
            novo.update({chave: ident, "vigente_de": d, "vigente_ate": None, "fonte": fonte, "confianca": conf})
            faltam = [c for c in tab.obrigatorias if novo.get(c) in (None, "")]
            if faltam:
                raise CenarioErro(f"{ev.get('evento_id')}: faltam campos obrigatórios {faltam}")
            refs = {"ramos": ("de", "para"), "usinas": ("barra_id",)}.get(ent, ())
            for c in refs:
                if not any(b["barra_id"] == novo[c] and _vigente(b, d) for b in linhas["barras"]):
                    raise CenarioErro(f"{ev.get('evento_id')}: barra {novo[c]} não existe em {d}")
            for c in tab.json:
                if novo.get(c) is not None and not isinstance(novo[c], str):
                    novo[c] = json.dumps(novo[c], ensure_ascii=False)
            rows.append(novo)
        else:
            ident = ev.get("entidade_id")
            atual = next((r for r in rows if r[chave] == ident and _vigente(r, d)), None)
            if atual is None:
                raise CenarioErro(f"{ev.get('evento_id')}: {ent} {ident} não está vigente em {d}")
            if acao == "sai":
                atual["vigente_ate"] = d
            else:
                campos = ev.get("campos") or {}
                fora = set(campos) - set(tab.nomes)
                if fora or set(campos) & CAMPOS_PROTEGIDOS or chave in campos:
                    raise CenarioErro(f"{ev.get('evento_id')}: campos inválidos {sorted(campos)}")
                if not campos:
                    raise CenarioErro(f"{ev.get('evento_id')}: 'altera' sem campos")
                campos_antes_depois = {c: [_jsonavel(atual.get(c)), v] for c, v in campos.items()}
                if atual["vigente_de"] == d:
                    atual.update(campos)
                    atual.update({"fonte": fonte, "confianca": conf})
                else:
                    nova = copy.deepcopy(atual)
                    nova.update(campos)
                    nova.update({"vigente_de": d, "fonte": fonte, "confianca": conf})
                    nova["vigente_ate"] = atual["vigente_ate"]
                    atual["vigente_ate"] = d
                    rows.append(nova)

        saida_eventos.append(
            {
                "evento_id": ev.get("evento_id"),
                "data": d,
                "acao": acao,
                "entidade": ent,
                "entidade_id": ident,
                "campos": json.dumps(campos_antes_depois, ensure_ascii=False) if campos_antes_depois else None,
                "fonte": fonte,
                "confianca": conf,
                "nota": ev.get("nota"),
            }
        )

    _posicionar_barras_novas(linhas["barras"], linhas["ramos"])
    tabelas = {n: pa.Table.from_pylist(linhas[n], schema=TABELAS[n].schema) for n in ENTIDADES}
    tabelas["eventos"] = pa.Table.from_pylist(saida_eventos, schema=TABELAS["eventos"].schema)
    return tabelas, saida_eventos


def _posicionar_barras_novas(barras: list[dict], ramos: list[dict]) -> None:
    """Barra nova sem posição: subestação conhecida, vizinhos, ou centro da UF."""
    pos_sub = {b["subestacao"]: (b["pos_x"], b["pos_y"]) for b in barras if b.get("pos_x") is not None}
    pos_barra = {b["barra_id"]: (b["pos_x"], b["pos_y"]) for b in barras if b.get("pos_x") is not None}
    for b in barras:
        if b.get("pos_x") is not None:
            continue
        p = pos_sub.get(b["subestacao"])
        if p is None:
            viz = [
                pos_barra[o]
                for r in ramos
                for o in (
                    (r["para"],) if r["de"] == b["barra_id"] else (r["de"],) if r["para"] == b["barra_id"] else ()
                )
                if o in pos_barra
            ]
            if viz:
                p = (sum(x for x, _ in viz) / len(viz) + 0.15, sum(y for _, y in viz) / len(viz) + 0.15)
            else:
                p = CENTRO_UF.get(b.get("uf") or "", (-47.9, -15.8))
        b["pos_x"], b["pos_y"] = p
        pos_sub.setdefault(b["subestacao"], p)
        pos_barra[b["barra_id"]] = p


class Cenarios:
    """Gerencia os cenários de uma pasta sobre um caso base."""

    def __init__(self, base: Cadastro, pasta: str | Path):
        self.base = base
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, Cadastro]] = {}
        self._lock = threading.RLock()

    # -- arquivos ---------------------------------------------------------
    def _arq(self, nome: str) -> Path:
        if not NOME_VALIDO.match(nome):
            raise CenarioErro("nome de cenário: letras minúsculas, números, '-' e '_' (até 64)")
        return self.pasta / f"{nome}.json"

    def ler(self, nome: str) -> dict:
        arq = self._arq(nome)
        if not arq.exists():
            raise KeyError(nome)
        return json.loads(arq.read_text())

    def _gravar(self, dados: dict) -> None:
        arq = self._arq(dados["nome"])
        tmp = arq.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(arq)

    def listar(self) -> list[dict]:
        out = [{"nome": BASE, "descricao": "Caso base, sem eventos", "pai": None, "n_eventos": 0, "editavel": False}]
        for arq in sorted(self.pasta.glob("*.json")):
            try:
                d = json.loads(arq.read_text())
            except ValueError:
                continue
            out.append(
                {
                    "nome": d["nome"],
                    "descricao": d.get("descricao", ""),
                    "pai": d.get("pai"),
                    "horizonte": d.get("horizonte"),
                    "n_eventos": len(d.get("eventos", [])),
                    "editavel": True,
                }
            )
        return out

    def cadeia(self, nome: str) -> list[dict]:
        """O cenário e seus pais, do mais baixo (perto do caso base) ao próprio."""
        out, vistos = [], set()
        atual: str | None = nome
        while atual and atual != BASE:
            if atual in vistos:
                raise CenarioErro(f"ciclo de pais em {nome}")
            vistos.add(atual)
            d = self.ler(atual)
            out.append(d)
            atual = d.get("pai")
        return list(reversed(out))

    # -- criação e edição -------------------------------------------------
    def criar(self, nome: str, descricao: str = "", pai: str | None = None, horizonte: str | None = None) -> dict:
        with self._lock:
            arq = self._arq(nome)
            if nome == BASE or arq.exists():
                raise CenarioErro(f"cenário {nome} já existe")
            if pai and pai != BASE:
                self.ler(pai)
            if horizonte:
                _d(horizonte)
            dados = {
                "nome": nome,
                "descricao": descricao,
                "pai": None if pai in (None, BASE) else pai,
                "horizonte": horizonte or self._horizonte_padrao(),
                "eventos": [],
            }
            self._gravar(dados)
            return dados

    def _horizonte_padrao(self) -> str:
        b = self.base.data_base() or date.today().isoformat()
        a, m, _ = b.split("-")
        return f"{int(a) + 5}-{m}-01"

    def apagar(self, nome: str) -> None:
        with self._lock:
            filhos = [c["nome"] for c in self.listar() if c.get("pai") == nome]
            if filhos:
                raise CenarioErro(f"o cenário {nome} é pai de {filhos}")
            self._arq(nome).unlink(missing_ok=False)
            self._cache.pop(nome, None)

    def adicionar_eventos(self, nome: str, novos: list[dict]) -> list[dict]:
        """Valida aplicando o cenário inteiro; só grava se tudo se aplicar."""
        with self._lock:
            dados = self.ler(nome)
            ids = {e["evento_id"] for c in self.cadeia(nome) for e in c.get("eventos", [])}
            prontos = []
            for ev in novos:
                ev = dict(ev)
                ev.setdefault("fonte", "usuario")
                ev.setdefault("confianca", "previsto")
                if ev.get("acao") not in ACOES:
                    raise CenarioErro(f"ação inválida: {ev.get('acao')!r}")
                ev["data"] = _d(ev.get("data")).isoformat()
                if not ev.get("evento_id"):
                    k = len(ids) + 1
                    while f"{nome}-{k}" in ids:
                        k += 1
                    ev["evento_id"] = f"{nome}-{k}"
                if ev["evento_id"] in ids:
                    raise CenarioErro(f"evento {ev['evento_id']} já existe")
                ids.add(ev["evento_id"])
                prontos.append(ev)
            candidato = {**dados, "eventos": dados.get("eventos", []) + prontos}
            # resolve ids automáticos de 'entra' para que fiquem estáveis no arquivo
            todos = [e for c in self.cadeia(nome)[:-1] for e in c.get("eventos", [])] + candidato["eventos"]
            _, normalizados = aplicar(self.base, todos)
            por_id = {e["evento_id"]: e for e in normalizados if e.get("evento_id")}
            for ev in prontos:
                if ev["acao"] == "entra" and not ev.get("entidade_id"):
                    ev["entidade_id"] = por_id[ev["evento_id"]]["entidade_id"]
                    tab = TABELAS[ev["entidade"]]
                    ev.setdefault("registro", {})[tab.chave] = ev["entidade_id"]
            self._gravar(candidato)
            self._cache.pop(nome, None)
            return prontos

    def remover_evento(self, nome: str, evento_id: str) -> None:
        with self._lock:
            dados = self.ler(nome)
            restantes = [e for e in dados.get("eventos", []) if e["evento_id"] != evento_id]
            if len(restantes) == len(dados.get("eventos", [])):
                raise KeyError(evento_id)
            candidato = {**dados, "eventos": restantes}
            todos = [e for c in self.cadeia(nome)[:-1] for e in c.get("eventos", [])] + restantes
            aplicar(self.base, todos)  # um evento posterior pode depender deste
            self._gravar(candidato)
            self._cache.pop(nome, None)

    # -- composição -------------------------------------------------------
    def cadastro(self, nome: str = BASE) -> Cadastro:
        if nome in (None, "", BASE):
            return self.base
        with self._lock:
            cadeia = self.cadeia(nome)
            marca = sum(self._arq(c["nome"]).stat().st_mtime for c in cadeia)
            em_cache = self._cache.get(nome)
            if em_cache and em_cache[0] == marca:
                return em_cache[1]
            eventos = [e for c in cadeia for e in c.get("eventos", [])]
            tabelas, _ = aplicar(self.base, eventos)
            info = dict(self.base.info)
            info["cenario"] = nome
            info["horizonte"] = cadeia[-1].get("horizonte")
            info["cadeia"] = [c["nome"] for c in cadeia]
            cad = Cadastro(pasta=self.base.pasta, tabelas=tabelas, info=info)
            self._cache[nome] = (marca, cad)
            return cad
