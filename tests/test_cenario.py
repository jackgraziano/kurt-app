from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kurt_app.api import criar_app
from kurt_app.cadastro import Cadastro
from kurt_app.cenario import CenarioErro, Cenarios, aplicar
from kurt_app.importar import eventos_de_linhas, eventos_de_usinas
from kurt_app.validar import validar

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acu3_20260910"


@pytest.fixture
def cens(tmp_path):
    return Cenarios(Cadastro(FIXTURE), tmp_path / "cen")


def linha_nova(data="2027-01-01", **kw):
    reg = {"de": "B5350", "para": "B5924", "tipo": "linha", "cap_normal_mva": 2000.0, "nome": "LT nova"}
    reg.update(kw)
    return {"data": data, "acao": "entra", "entidade": "ramos", "registro": reg, "confianca": "previsto"}


def test_base_intocado(cens):
    cens.criar("s1")
    cens.adicionar_eventos("s1", [linha_nova()])
    # a fixture já tem um circuito [DEMO] a partir de 2027-03-01
    assert len(cens.cadastro("base").configuracao("2027-06-01")["ramos"]) == 35
    assert len(cens.cadastro("s1").configuracao("2027-06-01")["ramos"]) == 36
    assert len(cens.cadastro("s1").configuracao("2026-12-01")["ramos"]) == 34


def test_id_automatico_fica_gravado(cens):
    cens.criar("s1")
    (ev,) = cens.adicionar_eventos("s1", [linha_nova()])
    assert ev["entidade_id"] == "R5350-5924-N1"
    assert cens.ler("s1")["eventos"][0]["registro"]["ramo_id"] == "R5350-5924-N1"
    (ev2,) = cens.adicionar_eventos("s1", [linha_nova()])
    assert ev2["entidade_id"] == "R5350-5924-N2"


def test_sai_e_altera(cens):
    cens.criar("s1")
    cens.adicionar_eventos(
        "s1",
        [
            {"data": "2027-01-01", "acao": "sai", "entidade": "ramos", "entidade_id": "R5350-5924-7"},
            {"data": "2027-03-01", "acao": "altera", "entidade": "usinas", "entidade_id": "U45125-1",
             "campos": {"cap_mw": 900.0}},
        ],
    )
    cad = cens.cadastro("s1")
    assert "R5350-5924-7" not in {r["ramo_id"] for r in cad.configuracao("2027-02-01")["ramos"]}
    assert "R5350-5924-7" in {r["ramo_id"] for r in cad.configuracao("2026-12-01")["ramos"]}
    caps = {d: next(u["cap_mw"] for u in cad.configuracao(d)["usinas"] if u["usina_id"] == "U45125-1")
            for d in ("2027-02-01", "2027-04-01")}
    assert caps == {"2027-02-01": 680.0, "2027-04-01": 900.0}
    ficha = cad.entidade("usinas", "U45125-1")
    assert len(ficha["versoes"]) == 2
    assert ficha["eventos"][-1]["campos"] == {"cap_mw": [680.0, 900.0]}
    assert [p for p in validar(cad) if p.severidade == "erro"] == []


def test_camadas(cens):
    cens.criar("expansao")
    cens.adicionar_eventos("expansao", [linha_nova("2027-01-01")])
    cens.criar("atraso", pai="expansao")
    # sensibilidade: a linha da expansão atrasa seis meses
    cens.adicionar_eventos("atraso", [{"data": "2027-01-01", "acao": "sai", "entidade": "ramos",
                                       "entidade_id": "R5350-5924-N1"}])
    cens.adicionar_eventos("atraso", [linha_nova("2027-07-01")])
    cad = cens.cadastro("atraso")
    ids = lambda d: {r["ramo_id"] for r in cad.configuracao(d)["ramos"]}  # noqa: E731
    assert "R5350-5924-N1" not in ids("2027-03-01")
    assert "R5350-5924-N2" in ids("2027-08-01")
    # mudar o pai invalida o cache do filho
    cens.adicionar_eventos("expansao", [linha_nova("2027-02-01", de="B5350", para="B5428")])
    assert any(r["para"] == "B5428" and r["fonte"] == "usuario"
               for r in cens.cadastro("atraso").configuracao("2027-03-01")["ramos"])


def test_erros(cens):
    cens.criar("s1")
    with pytest.raises(CenarioErro):
        cens.adicionar_eventos("s1", [linha_nova(de="B999")])  # barra inexistente
    with pytest.raises(CenarioErro):
        cens.adicionar_eventos("s1", [{"data": "2027-01-01", "acao": "sai", "entidade": "ramos", "entidade_id": "zz"}])
    with pytest.raises(CenarioErro):
        cens.adicionar_eventos("s1", [{"data": "2027-01-01", "acao": "altera", "entidade": "usinas",
                                       "entidade_id": "U45125-1", "campos": {"vigente_de": "2020-01-01"}}])
    with pytest.raises(CenarioErro):
        cens.criar("Nome Inválido")
    assert cens.ler("s1")["eventos"] == []  # nada gravado quando falha


def test_remover_evento_com_dependente(cens):
    cens.criar("s1")
    (ev,) = cens.adicionar_eventos("s1", [linha_nova()])
    cens.adicionar_eventos("s1", [{"data": "2027-05-01", "acao": "sai", "entidade": "ramos",
                                   "entidade_id": ev["entidade_id"]}])
    with pytest.raises(CenarioErro):
        cens.remover_evento("s1", ev["evento_id"])  # a saída depende da entrada


def test_barra_nova_ganha_posicao(cens):
    cens.criar("s1")
    cens.adicionar_eventos(
        "s1",
        [
            {"data": "2027-01-01", "acao": "entra", "entidade": "barras",
             "registro": {"barra_id": "BX1", "nome": "NOVA--RN500", "kv": 500.0, "subestacao": "NOVA", "uf": "RN"}},
            linha_nova(de="BX1", para="B5350"),
        ],
    )
    b = next(b for b in cens.cadastro("s1").configuracao("2027-02-01")["barras"] if b["barra_id"] == "BX1")
    assert b["pos_x"] is not None and b["pos_y"] is not None


def test_importar_csv():
    linhas = eventos_de_linhas(
        "data,de,para,tipo,circuitos,x_pct,cap_normal_mva,nome,fonte,confianca,nota\n"
        "2027-03-01,5350,5924,linha,2,1.2,2602,LT X,PAR/PEL,previsto,\n"
    )
    assert len(linhas) == 2 and linhas[0]["registro"]["de"] == "B5350"
    assert linhas[1]["registro"]["nome"] == "LT X (2)"
    usinas = eventos_de_usinas(
        "data,nome,fonte_energia,barra,cap_mw,flag_corte,fonte,confianca,nota\n"
        "2027-06-01,UFV Y,ufv,B5350,500,1,ANEEL,contratado,\n"
    )
    assert usinas[0]["registro"]["fonte_energia"] == "UFV"
    tabelas, _ = aplicar(Cadastro(FIXTURE), linhas + usinas)
    assert tabelas["ramos"].num_rows == 37 and tabelas["usinas"].num_rows == 42


def test_api_cenarios(tmp_path):
    cli = TestClient(criar_app(FIXTURE, tmp_path / "cen"))
    assert [c["nome"] for c in cli.get("/api/cenarios").json()] == ["base"]
    assert cli.post("/api/cenarios", json={"nome": "s1", "descricao": "teste"}).status_code == 201
    assert cli.post("/api/cenarios", json={"nome": "s1"}).status_code == 422
    r = cli.post("/api/cenarios/s1/eventos", json=linha_nova())
    assert r.status_code == 201
    ev = r.json()[0]
    c = cli.get("/api/configuracao", params={"data": "2027-02-01", "cenario": "s1"}).json()
    assert ev["entidade_id"] in {x["ramo_id"] for x in c["ramos"]}
    m = cli.get("/api/meta", params={"cenario": "s1"}).json()
    assert m["fim"] >= "2031-01-01" and m["data_base"] == "2026-09-10"
    assert cli.post("/api/cenarios/s1/eventos", json=linha_nova(de="B1")).status_code == 422
    r = cli.post("/api/cenarios/s1/importar", params={"tipo": "usinas"},
                 content="data,nome,fonte_energia,barra,cap_mw\n2027-06-01,UFV Z,UFV,5350,300\n",
                 headers={"content-type": "text/plain"})
    assert r.status_code == 201, r.text
    assert cli.get("/api/cenarios/s1").json()["eventos"][-1]["registro"]["nome"] == "UFV Z"
    assert cli.delete(f"/api/cenarios/s1/eventos/{ev['evento_id']}").status_code == 204
    assert cli.get("/api/configuracao", params={"data": "2027-02-01", "cenario": "nao"}).status_code == 404
    assert cli.get("/api/barras", params={"data": "2026-09-10", "q": "acu3"}).json()[0]["subestacao"] == "ACU3"
    assert cli.delete("/api/cenarios/s1").status_code == 204
