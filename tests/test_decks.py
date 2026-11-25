import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kurt_app import decks
from kurt_app.api import criar_app

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acu3_20260910"


def _deck(pasta: Path, nome: str, dia: int, mes: int, ano: int, rv: int = 0) -> None:
    (pasta / nome).mkdir(parents=True)
    (pasta / nome / f"dadger.rv{rv}").write_text(f"& teste\nTE  teste\nDT  {dia:02d}   {mes:02d}   {ano}\n")


@pytest.fixture
def pasta_decks(tmp_path):
    p = tmp_path / "decks"
    _deck(p, "DC122026", 28, 11, 2026)
    _deck(p, "DC012027", 26, 12, 2026)
    # março em zip; fevereiro falta de propósito
    with zipfile.ZipFile(p / "DC032027.zip", "w") as z:
        z.writestr("DC032027/dadger.rv0", "DT  27 02 2027\n")
    (p / "lixo").mkdir()
    return p


def test_semanas_operativas():
    s = decks.semanas_operativas(date(2026, 12, 1), date(2027, 1, 31))
    assert s[0] == {"inicio": "2026-11-28", "fim": "2026-12-04", "mes": "2026-12", "semana": 1}
    # a semana de 26/12 a 01/01 já é de janeiro
    assert [x["mes"] for x in s[:5]] == ["2026-12"] * 4 + ["2027-01"]
    assert s[4]["semana"] == 1
    assert len(decks.semanas_operativas_do_mes("2026-12")) == 4


def test_listar_pasta(pasta_decks):
    lista = decks.listar_pasta(pasta_decks)
    assert [(d.id, d.mes, d.revisao) for d in lista] == [
        ("DC122026", "2026-12", 0),
        ("DC012027", "2027-01", 0),
        ("DC032027", "2027-03", 0),
    ]


def test_mapear_repete_mes_sem_deck(pasta_decks):
    lista = decks.listar_pasta(pasta_decks)
    sem = decks.mapear(decks.semanas_operativas(date(2026, 12, 1), date(2027, 3, 31)), lista)
    dez = [x for x in sem if x["mes"] == "2026-12"]
    assert [x["estagio"] for x in dez] == [1, 2, 3, 4] and {x["deck"] for x in dez} == {"DC122026"}
    fev = [x for x in sem if x["mes"] == "2027-02"]
    assert fev and all(x["repetido"] and x["deck"] == "DC012027" for x in fev)
    assert all(not x["repetido"] for x in sem if x["mes"] == "2027-03")


def test_listar_ons(tmp_path):
    raiz = tmp_path / "ons"
    (raiz / "2025").mkdir(parents=True)
    for nome in [
        "20250201__DEC_ONS_022025_RV0_VE.zip",
        "20250208__DEC_ONS_022025_RV1_VE.zip",
        "20250301__DEC_ONS_032025_RV0_VE.zip",
        "20250201__RESULTADOS_DEC_ONS_022025_RV0_VE.zip",
    ]:
        (raiz / "2025" / nome).write_bytes(b"")
    lista = decks.listar_ons(raiz, date(2025, 2, 1), date(2025, 2, 28))
    assert [(d.mes, d.revisao) for d in lista] == [("2025-02", 0), ("2025-02", 1)]
    sem = decks.mapear(decks.semanas_operativas(date(2025, 2, 1), date(2025, 2, 28)), lista, so_rv0=False)
    assert [(x["deck"][-6:], x["estagio"]) for x in sem[:2]] == [("RV0_VE", 1), ("RV1_VE", 1)]


@pytest.fixture
def cli(tmp_path):
    return TestClient(criar_app(FIXTURE, tmp_path / "cen", tmp_path / "sim"))


def _criar(cli, pasta, **extra):
    return cli.post(
        "/api/simulacoes",
        json={"nome": "s1", "inicio": "2026-12-01", "fim": "2027-03-31", **extra}
        | {"decks": {"fonte": {"tipo": "pasta", "caminho": str(pasta)}}},
    )


def test_criar_com_decks_e_ida_e_volta(cli, pasta_decks):
    r = _criar(cli, pasta_decks)
    assert r.status_code == 201, r.text
    semanas = r.json()["insumos"]["decks"]["semanas"]
    assert semanas[0]["deck"] == "DC122026"
    doc = cli.get("/api/simulacoes/s1/insumos/decks").json()
    assert len(doc["dados"]["decks_disponiveis"]) == 3
    antes = cli.get("/api/simulacoes/s1").json()["insumos"]["decks"]
    r = cli.post("/api/simulacoes/s1/insumos/decks", json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["insumos"]["decks"] == antes


def test_trocar_deck_de_uma_semana(cli, pasta_decks):
    _criar(cli, pasta_decks)
    doc = cli.get("/api/simulacoes/s1/insumos/decks").json()
    doc["dados"]["semanas"][0]["deck"] = "DC012027"
    r = cli.post("/api/simulacoes/s1/insumos/decks", json=doc)
    assert r.json()["insumos"]["decks"]["semanas"][0]["deck"] == "DC012027"
    # sem 'semanas', o app refaz o mapa
    del doc["dados"]["semanas"]
    r = cli.post("/api/simulacoes/s1/insumos/decks", json=doc)
    assert r.json()["insumos"]["decks"]["semanas"][0]["deck"] == "DC122026"


def test_subida_invalida_nao_grava(cli, pasta_decks):
    _criar(cli, pasta_decks)
    antes = cli.get("/api/simulacoes/s1").json()
    doc = cli.get("/api/simulacoes/s1/insumos/decks").json()
    doc["dados"]["semanas"][3]["deck"] = "NAO_EXISTE"
    r = cli.post("/api/simulacoes/s1/insumos/decks", json=doc)
    assert r.status_code == 422 and "2026-12-19" in r.json()["detail"]
    assert cli.get("/api/simulacoes/s1").json() == antes


def test_criar_com_fonte_ruim_nao_cria(cli, tmp_path):
    r = _criar(cli, tmp_path / "nao-existe")
    assert r.status_code == 422
    assert cli.get("/api/simulacoes").json() == []


def test_listar_decks_api(cli, pasta_decks):
    r = cli.get(
        "/api/decks",
        params={"tipo": "pasta", "caminho": str(pasta_decks), "inicio": "2026-12-01", "fim": "2027-02-28"},
    )
    assert r.status_code == 200
    semanas = r.json()["semanas"]
    assert len(r.json()["decks"]) == 3
    assert all(x["repetido"] for x in semanas if x["mes"] == "2027-02")
