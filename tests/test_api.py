from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kurt_app.api import criar_app

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acu3_20260910"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    return TestClient(criar_app(FIXTURE, tmp_path_factory.mktemp("cen"), tmp_path_factory.mktemp("sim")))


def test_meta(cli):
    m = cli.get("/api/meta").json()
    assert m["inicio"] == "2026-09-10"
    assert m["linhas"]["barras"] == 31


def test_configuracao(cli):
    r = cli.get("/api/configuracao", params={"data": "2026-09-10", "kv_min": 500})
    assert r.status_code == 200
    c = r.json()
    assert all(b["kv"] >= 500 for b in c["barras"])
    assert c["usinas"] and all(u["barra_id"] in {b["barra_id"] for b in c["barras"]} for u in c["usinas"])


def test_erros(cli):
    assert cli.get("/api/configuracao", params={"data": "10/09/2026"}).status_code == 422
    assert cli.get("/api/entidade/barras/inexistente").status_code == 404
    assert cli.get("/api/entidade/coisas/x").status_code == 404
    assert cli.get("/api/eventos", params={"entidade": "coisas"}).status_code == 422


def test_eventos_e_diferenca(cli):
    evs = cli.get("/api/eventos", params={"de": "2027-01-01"}).json()
    assert [e["evento_id"] for e in evs] == ["E-DEMO-2", "E-DEMO-1"]
    d = cli.get("/api/diferenca", params={"a": "2026-09-10", "b": "2027-07-01"}).json()
    assert d["ramos"]["entraram"] == ["R5350-5924-D1"]
    assert d["usinas"]["entraram"] == ["U5350-D1"]


def test_validacao(cli):
    assert cli.get("/api/validacao").json()["erros"] == 0


def test_senha_opcional(tmp_path, monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    from kurt_app.api import criar_app

    arq = tmp_path / "senha"
    arq.write_text("kurt:segredo\n")
    monkeypatch.setenv("KURT_APP_SENHA_ARQUIVO", str(arq))
    c = TestClient(criar_app(None, tmp_path / "cen", tmp_path / "sim"))
    assert c.get("/api/simulacoes").status_code == 401
    ruim = base64.b64encode(b"kurt:errada").decode()
    assert c.get("/api/simulacoes", headers={"Authorization": f"Basic {ruim}"}).status_code == 401
    bom = base64.b64encode(b"kurt:segredo").decode()
    assert c.get("/api/simulacoes", headers={"Authorization": f"Basic {bom}"}).status_code == 200
