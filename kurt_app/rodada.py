"""Rodadas do Kurt para um cenário de simulação, e os resultados em P10, P50 e P90.

Uma rodada executa o plano da aba Execução: para cada dia do plano e cada
cenário de sorteio k (com 1 cenário, k = 0, a forma típica; com N > 1, k = 1..N,
dias históricos sorteados), grava
a pasta do dia (``dia.py``) e chama o executável do Kurt (``KURT_APP_RODAR_DIA``,
``scripts/rodar_dia.sh`` do repositório do Kurt) com o contrato:

    rodar_dia.sh <pasta_do_dia> <saida>

que deixa em ``<saida>``: ``corte.csv`` (``;``, colunas data, meia_hora, usina_id,
submercado, fonte, tipo, corte_mw, geracao_mw, disponivel_mw; tipo ENE ou rede),
``resumo.json`` (status, mensagem, custo, tempo_s, versao, avisos) e ``log.txt``.

Tudo fica em ``<simulações>/<cenário>/rodadas/<id>/``: ``rodada.json`` com o
estado de cada tarefa e uma pasta por tarefa (``<data>-c<k>/entrada`` e ``saida``).

Resultados: o corte de cada dia rodado (só o primeiro dia da pasta, o dia pedido)
vira energia (MWh), multiplicada pelo peso do dia (quantos dias do mês ele
representa) e somada por mês. Cada cenário k forma uma trajetória; P10, P50 e P90
são tirados entre os cenários.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from . import abas
from .dia import exportar_dia
from .simulacao import SimulacaoErro, Simulacoes

HORAS_MEIA = 0.5
TEMPO_MAX_S = 3 * 3600


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _gravar_json(arq: Path, dados: dict) -> None:
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    tmp.replace(arq)


class Rodadas:
    """Disparo, acompanhamento e resultados das rodadas, uma pasta por rodada."""

    def __init__(self, sims: Simulacoes, executavel: str | None = None):
        self.sims = sims
        self.executavel = executavel or os.environ.get("KURT_APP_RODAR_DIA")
        self._trava = threading.Lock()
        self._ativas: dict[str, dict] = {}  # id -> {"parar": Event, "procs": set}

    def _pasta(self, nome: str) -> Path:
        self.sims.ler(nome)  # KeyError se não existe
        return self.sims.pasta / nome / "rodadas"

    def listar(self, nome: str) -> list[dict]:
        pasta = self._pasta(nome)
        out = []
        for arq in sorted(pasta.glob("*/rodada.json"), reverse=True):
            r = json.loads(arq.read_text())
            out.append({k: v for k, v in r.items() if k != "tarefas"} | {"contagem": _contagem(r)})
        return out

    def ler(self, nome: str, rid: str) -> dict:
        arq = self._pasta(nome) / rid / "rodada.json"
        if not arq.exists():
            raise KeyError(rid)
        r = json.loads(arq.read_text())
        r["contagem"] = _contagem(r)
        r["ativa"] = f"{nome}|{rid}" in self._ativas
        return r

    # -- disparo ---------------------------------------------------------------
    def disparar(self, nome: str, esperar: bool = False) -> dict:
        if not self.executavel or not Path(self.executavel).is_file():
            raise SimulacaoErro(
                "executável do Kurt não configurado: aponte KURT_APP_RODAR_DIA para scripts/rodar_dia.sh do Kurt"
            )
        s = self.sims.ler(nome)
        if any(r.startswith(f"{nome}|") for r in self._ativas):
            raise SimulacaoErro(f"já há uma rodada em andamento para {nome}; espere ou cancele")
        op = abas._opcoes(s, "execucao")
        plano = abas.plano(s)
        if not plano:
            raise SimulacaoErro("o plano da aba Execução está vazio")
        rid = datetime.now().strftime("%Y%m%d-%H%M%S")
        pasta = self._pasta(nome) / rid
        pasta.mkdir(parents=True)
        tarefas = [
            {"data": p["data"], "peso": p["peso"], "tipo": p["tipo"], "cenario": k, "status": "na fila"}
            for p in plano
            for k in cenarios_da_rodada(op["cenarios"])
        ]
        r = {
            "id": rid,
            "cenario": nome,
            "criada_em": _agora(),
            "status": "rodando",
            "opcoes": op,
            "horizonte": {"inicio": s["inicio"], "fim": s["fim"]},
            "tarefas": tarefas,
        }
        _gravar_json(pasta / "rodada.json", r)
        chave = f"{nome}|{rid}"
        self._ativas[chave] = {"parar": threading.Event(), "procs": set()}
        t = threading.Thread(target=self._executar, args=(nome, rid, op["paralelo"]), daemon=True)
        t.start()
        if esperar:
            t.join()
        return self.ler(nome, rid)

    def cancelar(self, nome: str, rid: str) -> dict:
        ativa = self._ativas.get(f"{nome}|{rid}")
        if not ativa:
            raise SimulacaoErro(f"a rodada {rid} não está em andamento")
        ativa["parar"].set()
        for p in list(ativa["procs"]):
            p.terminate()
        return self.ler(nome, rid)

    def _atualizar(self, nome: str, rid: str, i: int | None, **campos) -> None:
        arq = self._pasta(nome) / rid / "rodada.json"
        with self._trava:
            r = json.loads(arq.read_text())
            if i is None:
                r.update(campos)
            else:
                r["tarefas"][i].update(campos)
            _gravar_json(arq, r)

    def _executar(self, nome: str, rid: str, paralelo: int) -> None:
        chave = f"{nome}|{rid}"
        ativa = self._ativas[chave]
        r = json.loads((self._pasta(nome) / rid / "rodada.json").read_text())
        try:
            with ThreadPoolExecutor(max_workers=paralelo) as ex:
                for i in range(len(r["tarefas"])):
                    ex.submit(self._tarefa, nome, rid, i, ativa)
            r = self.ler(nome, rid)
            c = r["contagem"]
            status = "cancelada" if ativa["parar"].is_set() else ("concluida" if c["erro"] == 0 else "com erros")
            self._atualizar(nome, rid, None, status=status, terminada_em=_agora())
        finally:
            self._ativas.pop(chave, None)

    def _tarefa(self, nome: str, rid: str, i: int, ativa: dict) -> None:
        if ativa["parar"].is_set():
            self._atualizar(nome, rid, i, status="cancelada")
            return
        r = json.loads((self._pasta(nome) / rid / "rodada.json").read_text())
        t = r["tarefas"][i]
        pasta = self._pasta(nome) / rid / f"{t['data']}-c{t['cenario']}"
        entrada, saida = pasta / "entrada", pasta / "saida"
        self._atualizar(nome, rid, i, status="rodando", inicio=_agora())
        t0 = time.monotonic()
        try:
            exportar_dia(self.sims, nome, t["data"], t["cenario"], entrada)
            saida.mkdir(parents=True, exist_ok=True)
            with open(pasta / "log.txt", "w") as log:
                p = subprocess.Popen([self.executavel, str(entrada), str(saida)], stdout=log, stderr=subprocess.STDOUT)
                ativa["procs"].add(p)
                try:
                    codigo = p.wait(timeout=TEMPO_MAX_S)
                except subprocess.TimeoutExpired:
                    p.kill()
                    codigo = -9
                finally:
                    ativa["procs"].discard(p)
            resumo = _ler_json(saida / "resumo.json")
            if ativa["parar"].is_set():
                status, msg = "cancelada", "cancelada pelo usuário"
            elif codigo == 0 and (saida / "corte.csv").exists():
                status, msg = "ok", resumo.get("mensagem", "")
            else:
                status = "erro"
                msg = resumo.get("mensagem") or f"o Kurt terminou com código {codigo}; veja {pasta / 'log.txt'}"
            self._atualizar(
                nome,
                rid,
                i,
                status=status,
                mensagem=msg,
                tempo_s=round(time.monotonic() - t0, 1),
                custo=resumo.get("custo"),
            )
        except Exception as e:  # uma tarefa que falha não derruba a rodada
            self._atualizar(nome, rid, i, status="erro", mensagem=str(e), tempo_s=round(time.monotonic() - t0, 1))

    # -- resultados ------------------------------------------------------------
    def resultados(self, nome: str, rid: str, n_usinas: int = 30) -> dict:
        r = self.ler(nome, rid)
        pasta = self._pasta(nome) / rid
        # energia cortada (MWh) por cenário, mês e chave, já com o peso do dia
        por_sub: dict[tuple, dict[int, float]] = {}
        por_usina: dict[tuple, dict[int, float]] = {}
        cenarios = sorted({t["cenario"] for t in r["tarefas"]})
        for t in r["tarefas"]:
            if t["status"] != "ok":
                continue
            arq = pasta / f"{t['data']}-c{t['cenario']}" / "saida" / "corte.csv"
            mes = t["data"][:7]
            k = t["cenario"]
            with open(arq, newline="") as f:
                for lin in csv.DictReader(f, delimiter=";"):
                    if lin.get("data", t["data"]) != t["data"]:
                        continue  # só o dia pedido; os outros dias da semana dão contorno ao deck
                    e = float(lin["corte_mw"] or 0) * HORAS_MEIA * t["peso"]
                    for d, chave in (
                        (por_sub, (mes, lin["submercado"], lin["tipo"])),
                        (por_sub, (mes, lin["submercado"], "total")),
                        (por_sub, (mes, "SIN", lin["tipo"])),
                        (por_sub, (mes, "SIN", "total")),
                        (por_usina, (lin["usina_id"], lin["submercado"], lin["fonte"])),
                    ):
                        d.setdefault(chave, {}).setdefault(k, 0.0)
                        d[chave][k] += e

        def quantis(v: dict[int, float]) -> dict:
            x = np.array([v.get(k, 0.0) for k in cenarios]) / 1000.0  # GWh
            return {
                "p10": round(float(np.percentile(x, 10)), 3),
                "p50": round(float(np.percentile(x, 50)), 3),
                "p90": round(float(np.percentile(x, 90)), 3),
                "media": round(float(x.mean()), 3),
            }

        meses = [{"mes": m, "submercado": s, "tipo": tp, **quantis(v)} for (m, s, tp), v in sorted(por_sub.items())]
        usinas = sorted(
            ({"usina_id": u, "submercado": s, "fonte": f, **quantis(v)} for (u, s, f), v in por_usina.items()),
            key=lambda x: -x["p50"],
        )[:n_usinas]
        return {
            "id": rid,
            "status": r["status"],
            "contagem": r["contagem"],
            "cenarios": len(cenarios),
            "unidade": "GWh",
            "meses": meses,
            "usinas": usinas,
        }


def cenarios_da_rodada(n: int) -> list[int]:
    """Com um cenário, roda o determinístico (forma típica); com mais, só dias históricos sorteados.

    A forma típica suaviza os picos de renováveis e subestima o corte (backtest de
    14 a 20/11/2026), então ela não entra nos quantis quando há sorteio.
    """
    return [0] if n <= 1 else list(range(1, n + 1))


def _contagem(r: dict) -> dict:
    c = {"total": len(r["tarefas"]), "ok": 0, "erro": 0, "rodando": 0, "na fila": 0, "cancelada": 0}
    for t in r["tarefas"]:
        c[t["status"]] = c.get(t["status"], 0) + 1
    return c


def _ler_json(arq: Path) -> dict:
    try:
        return json.loads(arq.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def apagar_rodadas(sims: Simulacoes, nome: str) -> None:
    shutil.rmtree(sims.pasta / nome, ignore_errors=True)
