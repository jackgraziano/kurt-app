from pathlib import Path

import pytest

from kurt_app.cadastro import Cadastro, CadastroErro
from kurt_app.escrita import escrever, tabela
from kurt_app.validar import validar

RAIZ = Path(__file__).resolve().parent.parent
FIXTURE = RAIZ / "fixtures" / "acu3_20260910"

R = {"fonte": "deck", "confianca": "realizado"}


def _cad(tmp_path, barras=None, ramos=None, usinas=None, restricoes=None, eventos=None):
    barras = (
        barras
        if barras is not None
        else [
            {
                "barra_id": "A",
                "nome": "A",
                "kv": 500.0,
                "subestacao": "SA",
                "uf": "RN",
                "vigente_de": "2025-01-01",
                **R,
            },
            {
                "barra_id": "B",
                "nome": "B",
                "kv": 500.0,
                "subestacao": "SB",
                "uf": "CE",
                "vigente_de": "2025-01-01",
                **R,
            },
            {
                "barra_id": "C",
                "nome": "C",
                "kv": 230.0,
                "subestacao": "SB",
                "uf": "CE",
                "vigente_de": "2025-06-01",
                **R,
            },
        ]
    )
    ramos = (
        ramos
        if ramos is not None
        else [
            {
                "ramo_id": "AB",
                "de": "A",
                "para": "B",
                "tipo": "linha",
                "cap_normal_mva": 1000.0,
                "vigente_de": "2025-01-01",
                "vigente_ate": "2025-09-01",
                **R,
            },
            {
                "ramo_id": "AB",
                "de": "A",
                "para": "B",
                "tipo": "linha",
                "cap_normal_mva": 1500.0,
                "vigente_de": "2025-09-01",
                **R,
            },
            {"ramo_id": "BC", "de": "B", "para": "C", "tipo": "trafo", "vigente_de": "2025-06-01", **R},
        ]
    )
    usinas = (
        usinas
        if usinas is not None
        else [
            {
                "usina_id": "U1",
                "nome": "U1",
                "fonte_energia": "EOL",
                "barra_id": "A",
                "cap_mw": 300.0,
                "vigente_de": "2025-01-01",
                **R,
            },
            {
                "usina_id": "U2",
                "nome": "U2",
                "fonte_energia": "UFV",
                "barra_id": "C",
                "cap_mw": 100.0,
                "vigente_de": "2025-06-01",
                "vigente_ate": "2026-01-01",
                **R,
            },
        ]
    )
    restricoes = (
        restricoes
        if restricoes is not None
        else [
            {
                "restr_id": "LPP1",
                "tipo": "LPP",
                "nome": "A-B",
                "termos": [{"elemento": "ramo", "id": "AB", "coeficiente": 1}],
                "vigente_de": "2025-01-01",
                **R,
            },
        ]
    )
    eventos = (
        eventos
        if eventos is not None
        else [
            {"evento_id": "E1", "data": "2025-06-01", "acao": "entra", "entidade": "barras", "entidade_id": "C", **R},
            {
                "evento_id": "E2",
                "data": "2025-09-01",
                "acao": "altera",
                "entidade": "ramos",
                "entidade_id": "AB",
                "campos": {"cap_normal_mva": [1000, 1500]},
                **R,
            },
        ]
    )
    escrever(
        tmp_path,
        {
            "barras": tabela("barras", barras),
            "ramos": tabela("ramos", ramos),
            "usinas": tabela("usinas", usinas),
            "restricoes": tabela("restricoes", restricoes),
            "eventos": tabela("eventos", eventos),
        },
    )
    return Cadastro(tmp_path)


def test_recorte_por_data(tmp_path):
    cad = _cad(tmp_path)
    c = cad.configuracao("2025-03-01")
    assert [b["barra_id"] for b in c["barras"]] == ["A", "B"]
    assert [u["usina_id"] for u in c["usinas"]] == ["U1"]
    assert c["ramos"][0]["cap_normal_mva"] == 1000.0
    c = cad.configuracao("2025-09-01")  # vigente_ate é exclusivo
    assert {r["ramo_id"]: r["cap_normal_mva"] for r in c["ramos"]} == {"AB": 1500.0, "BC": None}
    assert {u["usina_id"] for u in c["usinas"]} == {"U1", "U2"}
    assert [u["usina_id"] for u in cad.configuracao("2026-01-01")["usinas"]] == ["U1"]


def test_filtros(tmp_path):
    cad = _cad(tmp_path)
    c = cad.configuracao("2025-10-01", kv_min=500)
    assert [b["barra_id"] for b in c["barras"]] == ["A", "B"]
    assert [r["ramo_id"] for r in c["ramos"]] == ["AB"]  # o trafo para C sai junto
    assert [u["usina_id"] for u in c["usinas"]] == ["U1"]


def test_json_volta_como_objeto(tmp_path):
    cad = _cad(tmp_path)
    r = cad.configuracao("2025-03-01")["restricoes"][0]
    assert r["termos"] == [{"elemento": "ramo", "id": "AB", "coeficiente": 1}]


def test_diferenca(tmp_path):
    cad = _cad(tmp_path)
    d = cad.diferenca("2025-03-01", "2025-10-01")
    assert d["barras"]["entraram"] == ["C"]
    assert d["ramos"]["entraram"] == ["BC"]
    assert d["ramos"]["alterados"] == [{"id": "AB", "campos": {"cap_normal_mva": [1000.0, 1500.0]}}]
    assert d["usinas"]["entraram"] == ["U2"]


def test_entidade_e_eventos(tmp_path):
    cad = _cad(tmp_path)
    e = cad.entidade("ramos", "AB")
    assert len(e["versoes"]) == 2
    assert e["eventos"][0]["campos"] == {"cap_normal_mva": [1000, 1500]}
    assert [x["evento_id"] for x in cad.eventos(de="2025-07-01")] == ["E2"]
    with pytest.raises(KeyError):
        cad.entidade("ramos", "nao-existe")


def test_meta(tmp_path):
    m = _cad(tmp_path).meta()
    assert m["inicio"] == "2025-01-01"
    assert m["fim"] == "2026-01-01"
    assert [x["mes"] for x in m["eventos_por_mes"]] == ["2025-06", "2025-09"]


def test_validacao_limpa(tmp_path):
    assert validar(_cad(tmp_path)) == []


def test_validacao_pega_sobreposicao(tmp_path):
    ramos = [
        {"ramo_id": "AB", "de": "A", "para": "B", "tipo": "linha", "vigente_de": "2025-01-01", **R},
        {"ramo_id": "AB", "de": "A", "para": "B", "tipo": "linha", "vigente_de": "2025-09-01", **R},
        {"ramo_id": "BC", "de": "B", "para": "C", "tipo": "trafo", "vigente_de": "2025-06-01", **R},
    ]
    probs = validar(_cad(tmp_path, ramos=ramos))
    assert any(p.checagem == "vigencia" and p.severidade == "erro" for p in probs)


def test_validacao_pega_barra_isolada_e_referencia(tmp_path):
    ramos = [{"ramo_id": "AB", "de": "A", "para": "B", "tipo": "linha", "vigente_de": "2025-01-01", **R}]
    usinas = [
        {"usina_id": "U2", "nome": "U2", "fonte_energia": "UFV", "barra_id": "C", "vigente_de": "2025-06-01", **R},
        {"usina_id": "U3", "nome": "U3", "fonte_energia": "UFV", "barra_id": "X", "vigente_de": "2025-06-01", **R},
    ]
    probs = validar(_cad(tmp_path, ramos=ramos, usinas=usinas))
    checs = {p.checagem for p in probs if p.severidade == "erro"}
    assert {"conexidade", "referencia"} <= checs


def test_coluna_fora_do_esquema(tmp_path):
    with pytest.raises(ValueError):
        tabela("barras", [{"barra_id": "A", "inventada": 1}])


def test_pasta_inexistente(tmp_path):
    with pytest.raises(CadastroErro):
        Cadastro(tmp_path / "nada")


def test_fixture_acu3_valida():
    cad = Cadastro(FIXTURE)
    assert [p for p in validar(cad) if p.severidade == "erro"] == []
    c = cad.configuracao("2026-09-10")
    assert len(c["barras"]) == 31
    assert {r["restr_id"] for r in c["restricoes"]} == {"LPP22", "LPP23", "LPP26"}
    # os eventos de demonstração só valem no futuro
    assert not any(u["nome"].startswith("[DEMO]") for u in c["usinas"])
    assert any(u["nome"].startswith("[DEMO]") for u in cad.configuracao("2027-07-01")["usinas"])


def test_inferir_uf_pelos_vizinhos():
    from kurt_app.layout import inferir_ufs

    subs = {"A": "RN", "B": "RN", "C": "CE", "X": None, "Y": None, "Z": None}
    ar = [("A", "X"), ("B", "X"), ("C", "X"), ("X", "Y")]
    uf = inferir_ufs(subs, ar)
    assert uf["X"] == "RN" and uf["Y"] == "RN" and uf["Z"] is None
