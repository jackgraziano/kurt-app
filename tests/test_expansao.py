"""Expansão (RALIE e SIGET) com dados sintéticos, sem rede."""

from datetime import date

from kurt_app.expansao import montar, normalizar, score_codigo, tokens
from kurt_app.transmissao import montar as montar_transmissao


def barra(num, nome, sub, kv, uf="RN", subm="NE"):
    return {"barra_id": f"B{num}", "num_pwf": num, "nome": nome, "kv": kv, "subestacao": sub, "uf": uf, "submercado": subm, "area": 1}


BASE = {
    "barras": [
        barra(1, "J.CAM3-RN500", "J.CAM3", 500.0),
        barra(2, "JCAMAR-RN230", "JCAMAR", 230.0),
        barra(3, "ACU3---RN500", "ACU3", 500.0),
        barra(4, "ACU3---RN230", "ACU3", 230.0),
        barra(5, "CEAMIR-RN500", "CEAMIR", 500.0),
    ],
    "usinas": [
        {"usina_id": "EOL-7", "nome": "CNLBI  _CJ LAGOA DO BARRO I_05040_UEE", "fonte_energia": "EOL", "barra_id": "B4"},
    ],
    "ramos": [
        {"ramo_id": "R1-5-1", "de": "B1", "para": "B5", "circuito": 1, "tipo": "linha", "x_pct": 1.0, "cap_normal_mva": 2000.0, "cap_emerg_mva": 2400.0},
        {"ramo_id": "R3-4-1", "de": "B3", "para": "B4", "circuito": 1, "tipo": "trafo", "x_pct": 0.8, "cap_normal_mva": 900.0, "cap_emerg_mva": 900.0},
    ],
}


def test_normalizacao():
    assert tokens("SE Açu III") == ["ACU", "3"]
    assert normalizar("Subestação São João do Piauí II") == "S JOAO PIAUI 2"
    assert normalizar("Ceará-Mirim, II.") == "CEARA MIRIM 2"


def test_score_codigo():
    assert score_codigo("João Câmara III", "J.CAM3") > 0.9
    assert score_codigo("João Câmara III", "J.CAM3") > score_codigo("João Câmara III", "JCAMAR")
    assert score_codigo("Açu III", "ACU2") == 0  # número diferente
    assert score_codigo("Morro do Chapéu II", "M.CHAP") > 0.7
    assert score_codigo("Paranaíba III", "PNBIII") >= 0.75  # romano no código


def usina(cod, nome, conexao, kv, fonte="EOL", obra="Em andamento", **kw):
    u = {
        "CodCEG": cod, "IdeNucleoCEG": cod[-3:], "NomEmpreendimento": nome, "NomConexao": conexao,
        "MdaTensaoConexao": kv, "SigUFPrincipal": "RN", "SigTipoGeracao": fonte, "DscOrigemCombustivel": "Eólica",
        "DscSituacaoObra": obra, "DscSistema": "Sistema Interligado Nacional", "DscTipoConexao": "Subestação",
        "NomComplexo": "", "DscViabilidade": "Média",
    }
    u.update(kw)
    return u


def ug(cod, kw, data):
    return {"CodCEG": cod, "MdaPotenciaUnitaria": kw, "DatPrevisaoOpComercialSFG": data, "DatUGInicioOpComerOutorgado": ""}


def test_eventos_ralie():
    usinas = [
        usina("EOL.CV.RN.000101", "Ventos Novos", "João Câmara III", "500,00"),
        usina("UFV.RS.RN.000102", "Sol Sem Ponto", "Não definida na outorga", ",00", fonte="UFV", obra="Não Iniciada"),
        usina("EOL.CV.RN.000103", "Lagoa do Barro I", "Açu III", "230,00"),
    ]
    ugs = [
        ug("EOL.CV.RN.000101", "4500,00", "2027-03-15"),
        ug("EOL.CV.RN.000101", "4500,00", "2027-03-20"),
        ug("EOL.CV.RN.000101", "6000,00", "2027-07-01"),
        ug("EOL.CV.RN.000101", "6000,00", "2033-01-01"),  # depois do horizonte
        ug("UFV.RS.RN.000102", "50000,00", "2028-01-01"),
        ug("EOL.CV.RN.000103", "4000,00", "2020-01-01"),  # atrasada
    ]
    r = montar(usinas, ugs, BASE, date(2026, 11, 20), "ANEEL RALIE teste")
    evs = [e for e in r["eventos"] if e["entidade_id"] == "EOL-A101"]
    assert [(e["acao"], e["data"]) for e in evs] == [("entra", "2027-03-01"), ("altera", "2027-07-01")]
    assert evs[0]["registro"]["barra_id"] == "B1"
    assert evs[0]["registro"]["cap_mw"] == 9.0 and evs[1]["campos"]["cap_mw"] == 15.0
    assert evs[0]["registro"]["flag_corte"] == 1 and evs[0]["confianca"] == "contratado"
    assert [p["CodCEG"] for p in r["pendencias"]] == ["UFV.RS.RN.000102"]
    # Lagoa do Barro I já está no deck na mesma subestação
    assert [d["usina_deck"] for d in r["no_deck"]] == ["EOL-7"]


def obra(mdl, nome, desc, data="30/06/2028"):
    return {"IdeMdl": mdl, "IdeOnsMdl": f"M{mdl}", "NomMdl": nome, "DscObr": desc, "DscSitObr": "Em andamento", "DscTipObr": "Instalação", "SigTipMdl": nome.split()[0] if nome.startswith("LT") else "ME", "DatOprComEpd": data, "DatOprComObr": ""}


def modulo(mdl, a, b, kv="500,00", km="100,0", c="1"):
    return {"IdeMdl": mdl, "NomLinTms": f"LT {kv[:3]} kV {a} /{b} RN", "NomSubestacaoOrigem": a, "SigUFSubestacaoOrigem": "RN", "NomSubestacaoDestino": b, "SigUFSubestacaoDestino": "RN", "NumTensaoBaseLinhaTransm": kv, "NumEtnLinTms": km, "NumCcuLinTms": c}


def test_eventos_transmissao():
    obras = [
        obra("1", "LT 500 kV ACU III /CEARA MIRIM C-1 RN", "Construção da LT 500 kV, 1195/1350 MVA"),
        obra("2", "LT 500 kV JOAO CAMARA III /SERRA NOVA C-1 RN", "Instalar o circuito LT 500 kV JOAO CAMARA III /SERRA NOVA C-1 RN originado do seccionamento do circuito LT 500 kV JOAO CAMARA III /CEARA MIRIM C-1 RN na SE Serra Nova"),
        obra("3", "LT 500 kV SERRA NOVA /CEARA MIRIM C-1 RN", "Instalar o circuito LT 500 kV SERRA NOVA /CEARA MIRIM C-1 RN originado do seccionamento do circuito LT 500 kV JOAO CAMARA III /CEARA MIRIM C-1 RN na SE Serra Nova"),
        obra("4", "TR 500/230 kV ACU III TR2 RN", ""),
    ]
    mods = [modulo("1", "ACU III", "CEARA MIRIM"), modulo("2", "JOAO CAMARA III", "SERRA NOVA", km="30,0"), modulo("3", "SERRA NOVA", "CEARA MIRIM", km="70,0")]
    r = montar_transmissao(obras, mods, BASE, date(2026, 11, 20), "ANEEL SIGET teste")
    acoes = sorted((e["acao"], e["entidade"]) for e in r["eventos"])
    assert acoes == [("entra", "barras"), ("entra", "ramos"), ("entra", "ramos"), ("entra", "ramos"), ("entra", "ramos"), ("sai", "ramos")]
    lt = next(e for e in r["eventos"] if e["entidade_id"].endswith("S1"))
    assert {lt["registro"]["de"], lt["registro"]["para"]} == {"B3", "B5"} and lt["registro"]["cap_normal_mva"] == 1195.0
    assert next(e for e in r["eventos"] if e["acao"] == "sai")["entidade_id"] == "R1-5-1"
    xs = sorted(e["registro"]["x_pct"] for e in r["eventos"] if e["acao"] == "entra" and e["entidade_id"].endswith(("S2", "S3")))
    assert xs == [0.3, 0.7]
    tr = next(e for e in r["eventos"] if e.get("registro", {}).get("tipo") == "trafo")
    assert tr["registro"]["x_pct"] == 0.8 and r["contagem"]["seccionamentos"] == 1
