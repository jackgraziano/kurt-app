from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kurt_app.api import criar_app

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acu3_20260910"


@pytest.fixture
def cli(tmp_path):
    c = TestClient(criar_app(FIXTURE, tmp_path / "cen", tmp_path / "sim"))
    c.post("/api/cenarios", json={"nome": "expansao"})
    c.post(
        "/api/cenarios/expansao/eventos",
        json={
            "data": "2027-04-01",
            "acao": "entra",
            "entidade": "ramos",
            "registro": {"de": "B5350", "para": "B5428", "tipo": "linha", "cap_normal_mva": 2000},
        },
    )
    c.post("/api/simulacoes", json={"nome": "s1", "caso_rede": "expansao"})
    return c


def test_baixar_rede(cli):
    r = cli.get("/api/simulacoes/s1/insumos/rede", params={"baixar": 1})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    doc = r.json()
    assert doc["formato"] == "kurt-app/insumo" and doc["insumo"] == "rede" and doc["_ajuda"]
    assert doc["dados"]["caso_de_origem"] == "expansao"
    [ev] = doc["dados"]["eventos"]
    assert ev["camada"] == "expansao" and ev["entidade_id"]


def test_subir_rede_editada(cli):
    doc = cli.get("/api/simulacoes/s1/insumos/rede").json()
    ev = doc["dados"]["eventos"][0]
    ev["data"] = "2027-09-01"  # atrasa a linha
    doc["dados"]["eventos"].append(
        {
            "data": "2027-10-01",
            "acao": "altera",
            "entidade": "ramos",
            "entidade_id": ev["entidade_id"],
            "campos": {"cap_normal_mva": 2400},
        }
    )
    r = cli.post("/api/simulacoes/s1/insumos/rede", json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["insumos"]["rede"]["caso"] == "s1-rede"

    # o caso compartilhado não mudou; o caso próprio tem as duas mudanças
    assert cli.get("/api/cenarios/expansao").json()["eventos"][0]["data"] == "2027-04-01"
    proprio = cli.get("/api/cenarios/s1-rede").json()
    assert [e["data"] for e in proprio["eventos"]] == ["2027-09-01", "2027-10-01"]
    cfg = cli.get("/api/configuracao", params={"data": "2027-10-31", "cenario": "s1-rede"}).json()
    [ramo] = [x for x in cfg["ramos"] if x["ramo_id"] == ev["entidade_id"]]
    assert ramo["cap_normal_mva"] == 2400

    # subir de novo sobrescreve o caso próprio
    doc2 = cli.get("/api/simulacoes/s1/insumos/rede").json()
    assert doc2["dados"]["caso_de_origem"] == "s1-rede"
    doc2["dados"]["eventos"] = doc2["dados"]["eventos"][:1]
    assert cli.post("/api/simulacoes/s1/insumos/rede", json=doc2).status_code == 200
    assert len(cli.get("/api/cenarios/s1-rede").json()["eventos"]) == 1

    # apagar o cenário leva junto o caso de rede que era só dele
    assert cli.delete("/api/simulacoes/s1").status_code == 204
    assert cli.get("/api/cenarios/s1-rede").status_code == 404
    assert cli.get("/api/cenarios/expansao").status_code == 200


@pytest.mark.parametrize(
    "mexer, trecho",
    [
        (lambda d: d.update(formato="outro"), "não é um JSON de insumo"),
        (lambda d: d.update(insumo="carga"), "da aba 'carga'"),
        (lambda d: d["dados"].update(eventos="x"), "lista de objetos"),
        (lambda d: d["dados"]["eventos"][0].update(entidade_id="R-nao-existe", acao="sai"), "R-nao-existe"),
        (lambda d: d["dados"]["eventos"][0]["registro"].update(de="B99999"), "B99999"),
    ],
)
def test_subir_rede_invalida(cli, mexer, trecho):
    doc = cli.get("/api/simulacoes/s1/insumos/rede").json()
    mexer(doc)
    r = cli.post("/api/simulacoes/s1/insumos/rede", json=doc)
    assert r.status_code == 422 and trecho in r.json()["detail"], r.text
    assert cli.get("/api/simulacoes/s1").json()["insumos"]["rede"]["caso"] == "expansao"


def test_aba_sem_decks(cli):
    r = cli.get("/api/simulacoes/s1/insumos/carga")
    assert r.status_code == 422 and "aba Decks" in r.json()["detail"]
    assert cli.get("/api/simulacoes/s1/insumos/vento").status_code == 404
