from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kurt_app.api import criar_app
from kurt_app.simulacao import IDS_INSUMOS, horizonte_padrao

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acu3_20260910"


@pytest.fixture
def cli(tmp_path):
    return TestClient(criar_app(FIXTURE, tmp_path / "cen", tmp_path / "sim"))


def test_horizonte_padrao():
    assert horizonte_padrao("2026-11-20") == ("2026-12-01", "2027-11-30")
    assert horizonte_padrao("2026-12-05") == ("2027-01-01", "2027-12-31")


def test_insumos(cli):
    ins = cli.get("/api/insumos").json()
    assert [i["id"] for i in ins] == list(IDS_INSUMOS)
    assert [i["id"] for i in ins if i["disponivel"]] == ["rede"]


def test_ciclo_de_vida(cli):
    assert cli.get("/api/simulacoes").json() == []
    s = cli.post("/api/simulacoes", json={"nome": "nov26", "descricao": "teste"}).json()
    assert (s["inicio"], s["fim"]) == ("2026-10-01", "2027-09-30")  # caso base da fixture: 10/09/2026
    assert s["insumos"]["rede"] == {"caso": "base"}
    assert set(s["insumos"]) == set(IDS_INSUMOS)

    cli.post("/api/cenarios", json={"nome": "expansao"})
    s = cli.patch("/api/simulacoes/nov26", json={"fim": "2028-12-31", "insumos": {"rede": {"caso": "expansao"}}}).json()
    assert s["fim"] == "2028-12-31" and s["insumos"]["rede"]["caso"] == "expansao"
    assert s["insumos"]["carga"] == {}  # as outras abas ficam como estavam

    # o caso de rede em uso não pode ser apagado
    r = cli.delete("/api/cenarios/expansao")
    assert r.status_code == 422 and "nov26" in r.json()["detail"]

    assert [x["nome"] for x in cli.get("/api/simulacoes").json()] == ["nov26"]
    assert cli.delete("/api/simulacoes/nov26").status_code == 204
    assert cli.get("/api/simulacoes/nov26").status_code == 404
    assert cli.delete("/api/cenarios/expansao").status_code == 204


@pytest.mark.parametrize(
    "corpo, trecho",
    [
        ({"nome": "Maiúsculo"}, "nome inválido"),
        ({"nome": "x", "inicio": "2027-01-01", "fim": "2026-01-01"}, "antes do início"),
        ({"nome": "x", "inicio": "2027-01-01", "fim": "2045-01-01"}, "maior que"),
        ({"nome": "x", "caso_rede": "nao-existe"}, "não existe"),
        ({"nome": "x", "inicio": "01/2027"}, "inválido"),
    ],
)
def test_erros(cli, corpo, trecho):
    r = cli.post("/api/simulacoes", json=corpo)
    assert r.status_code == 422 and trecho in r.json()["detail"]
    assert cli.get("/api/simulacoes").json() == []


def test_insumo_desconhecido_e_duplicado(cli):
    cli.post("/api/simulacoes", json={"nome": "a"})
    assert cli.post("/api/simulacoes", json={"nome": "a"}).status_code == 422
    r = cli.patch("/api/simulacoes/a", json={"insumos": {"vento": {}}})
    assert r.status_code == 422 and "vento" in r.json()["detail"]
