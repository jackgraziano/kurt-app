"""Disparo de rodadas com um executável falso que segue o contrato de rodar_dia.sh."""

import stat
import sys

import pytest
from test_abas_decomp import cli as cli

from kurt_app.rodada import Rodadas

FALSO = """#!{py}
import csv, json, sys, pathlib
entrada, saida = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
dia = json.loads((entrada / "dia.json").read_text())
if dia["data"] == "{falha}":
    print("deu ruim"); sys.exit(3)
k = dia["cenario"]
with open(saida / "corte.csv", "w", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["data", "meia_hora", "usina_id", "submercado", "fonte", "tipo", "corte_mw", "geracao_mw", "disponivel_mw"])
    for d in dia["dias"]:
        for h in range(48):
            w.writerow([d["data"], h, "U5441-1", "NE", "EOL", "rede", 100 * (k + 1), 0, 0])
            w.writerow([d["data"], h, "U5441-2", "NE", "EOL", "ENE", 10, 0, 0])
(saida / "resumo.json").write_text(json.dumps({{"status": "ok", "custo": 1.0, "mensagem": ""}}))
"""


def _falso(tmp_path, falha=""):
    arq = tmp_path / "rodar_dia.py"
    arq.write_text(FALSO.format(py=sys.executable, falha=falha))
    arq.chmod(arq.stat().st_mode | stat.S_IEXEC)
    return str(arq)


def _poucos(cli):
    doc = cli.get("/api/simulacoes/s1/insumos/execucao").json()
    doc["dados"]["opcoes"] |= {"cenarios": 3, "paralelo": 2}
    assert cli.post("/api/simulacoes/s1/insumos/execucao", json=doc).status_code == 200


def test_rodada_e_resultados(cli, tmp_path):
    _poucos(cli)
    rod = Rodadas(cli.app.state.simulacoes, _falso(tmp_path))
    r = rod.disparar("s1", esperar=True)
    assert r["status"] == "concluida" and r["contagem"]["ok"] == 9  # 3 dias × 3 cenários
    res = rod.resultados("s1", r["id"])
    sin = {(m["submercado"], m["tipo"]): m for m in res["meses"] if m["mes"] == "2026-12"}
    # rede: 100·(k+1) MW × 24 h × 31 dias, em GWh; k = 1..3 e P50 é o do meio (k = 2)
    assert sin[("NE", "rede")]["p50"] == pytest.approx(300 * 24 * 31 / 1000)
    assert sin[("NE", "ENE")]["p10"] == pytest.approx(10 * 24 * 31 / 1000)
    assert res["usinas"][0]["usina_id"] == "U5441-1"
    assert rod.listar("s1")[0]["contagem"]["ok"] == 9


def test_tarefa_com_erro_nao_derruba(cli, tmp_path):
    _poucos(cli)
    plano = cli.get("/api/simulacoes/s1/insumos/execucao").json()["dados"]["plano"]
    alvo = plano[0]["data"]
    rod = Rodadas(cli.app.state.simulacoes, _falso(tmp_path, falha=alvo))
    r = rod.disparar("s1", esperar=True)
    assert r["status"] == "com erros" and r["contagem"]["erro"] == 3 and r["contagem"]["ok"] == 6
    t = next(t for t in r["tarefas"] if t["status"] == "erro")
    assert "código 3" in t["mensagem"]


def test_sem_executavel(cli, monkeypatch):
    monkeypatch.delenv("KURT_APP_RODAR_DIA", raising=False)
    r = cli.post("/api/simulacoes/s1/rodadas")
    assert r.status_code == 422 and "KURT_APP_RODAR_DIA" in r.json()["detail"]
    assert cli.get("/api/simulacoes/s1/rodadas").json() == []
