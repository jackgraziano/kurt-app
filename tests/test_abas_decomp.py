"""Abas que vêm do DECOMP (carga, renováveis, térmicas, hidro, valor da água, execução)
e a pasta do dia que o Kurt recebe. Usa o deck mínimo e formas sintéticas."""

import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from kurt_app import dia, niveis, series
from kurt_app.api import criar_app
from kurt_app.fontes.formas import tipo_dia

RAIZ = Path(__file__).resolve().parent.parent
FIXTURE = RAIZ / "fixtures" / "acu3_20260910"
MINI = RAIZ / "fixtures" / "decomp_mini"
SUBS = ("SE", "S", "NE", "N")


def _formas(pasta: Path) -> None:
    pasta.mkdir()
    meses = (11, 12)
    forma = 1 + 0.3 * np.sin(np.linspace(0, 2 * np.pi, 48, endpoint=False) - np.pi / 2)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"submercado": s, "mes": m, "tipo_dia": t, "meia_hora": h, "fator": float(forma[h] * k)}
                for s in SUBS
                for m in meses
                for t, k in (("util", 1.05), ("sabado", 0.95), ("domingo", 0.85))
                for h in range(48)
            ]
        ),
        pasta / "carga.parquet",
    )
    sol = np.clip(np.sin(np.linspace(-np.pi / 2, 3 * np.pi / 2, 48, endpoint=False)), 0, None) * 3
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"submercado": s, "fonte": "UFV", "mes": m, "meia_hora": h, "fc": float(sol[h])}
                for s in SUBS
                for m in meses
                for h in range(48)
            ]
        ),
        pasta / "mmgd.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"fonte": f, "uf": "BR", "mes": m, "hora": h, "fc": float(v)}
                for f, v in (("EOL", 0.45), ("UFV", 0.25))
                for m in meses
                for h in range(24)
            ]
        ),
        pasta / "fc_grupos.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "usina_id": pa.array([], pa.string()),
                "id_ons": pa.array([], pa.string()),
                "via": pa.array([], pa.string()),
                "fonte": pa.array([], pa.string()),
                "uf_grupo": pa.array([], pa.string()),
            }
        ),
        pasta / "mapa_usinas.parquet",
    )
    rng = np.random.default_rng(1)
    linhas = []
    for d in range(1, 29):
        dd = date(2025, 12, d)
        r = {"data": dd, "mes": 12, "tipo_dia": tipo_dia(dd)}
        for s in SUBS:
            r[f"carga_{s}"] = (forma * rng.uniform(0.9, 1.1)).tolist()
            r[f"fc_EOL_{s}"] = (np.full(48, 0.45) * rng.uniform(0.5, 1.5)).tolist()
        linhas.append(r)
    pq.write_table(pa.Table.from_pylist(linhas), pasta / "dias.parquet")


@pytest.fixture
def cli(tmp_path, monkeypatch):
    decks = tmp_path / "decks"
    shutil.copytree(MINI, decks / "DC122026")
    _formas(tmp_path / "formas")
    monkeypatch.setenv("KURT_APP_FORMAS", str(tmp_path / "formas"))
    niveis._formas.cache_clear()
    c = TestClient(criar_app(FIXTURE, tmp_path / "cen", tmp_path / "sim"))
    r = c.post(
        "/api/simulacoes",
        json={
            "nome": "s1",
            "inicio": "2026-12-01",
            "fim": "2026-12-31",
            "decks": {"fonte": {"tipo": "pasta", "caminho": str(decks)}},
        },
    )
    assert r.status_code == 201, r.text
    return c


ABAS = ("carga", "renovaveis", "termicas", "hidro", "valor_agua", "execucao")


@pytest.mark.parametrize("aba", ABAS)
def test_ida_e_volta(cli, aba):
    doc = cli.get(f"/api/simulacoes/s1/insumos/{aba}").json()
    r = cli.post(f"/api/simulacoes/s1/insumos/{aba}", json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["insumos"][aba]["sobrescritos"] == {}
    doc2 = cli.get(f"/api/simulacoes/s1/insumos/{aba}").json()
    assert doc2["dados"] == doc["dados"]


def test_carga_sobrescrito_so_do_que_mudou(cli):
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    carga = doc["dados"]["carga"]
    assert {r["submercado"] for r in carga} == set(SUBS)
    alvo = next(r for r in carga if r["submercado"] == "NE" and r["patamar"] == 1)
    alvo["mwmed"] = 20000.0
    r = cli.post("/api/simulacoes/s1/insumos/carga", json=doc)
    sob = r.json()["insumos"]["carga"]["sobrescritos"]
    assert sob == {"carga": [{"semana": alvo["semana"], "submercado": "NE", "patamar": 1, "mwmed": 20000.0}]}


def test_subida_invalida_nao_grava(cli):
    antes = cli.get("/api/simulacoes/s1").json()
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    doc["dados"]["carga"][0]["semana"] = "2030-01-05"
    r = cli.post("/api/simulacoes/s1/insumos/carga", json=doc)
    assert r.status_code == 422 and "não existe no DECOMP" in r.json()["detail"]
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    doc["dados"]["carga"][0]["mwmed"] = "muito"
    assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc).status_code == 422
    doc = cli.get("/api/simulacoes/s1/insumos/execucao").json()
    doc["dados"]["opcoes"]["dias"] = "alguns"
    assert cli.post("/api/simulacoes/s1/insumos/execucao", json=doc).status_code == 422
    assert cli.get("/api/simulacoes/s1").json() == antes


def test_series_batem_com_o_decomp(cli, monkeypatch):
    sims = cli.app.state.simulacoes
    s = sims.ler("s1")
    x = niveis.semanas(s)[0]
    # na fixture o fator do NE EOL é 0,32: fora da faixa, fica limitado a 0,5 e avisado
    assert dia.semana(sims, s, x)["fator_renovaveis"]["NE|EOL"] == 0.5
    monkeypatch.setattr(series, "FAIXA_FATOR", (0.0, 1e9))
    sem = dia.semana(sims, s, x)
    pat = sem["patamar"]
    horas = x["horas"]
    assert [(pat == p).sum() for p in (1, 2, 3)] == [round(336 * h / sum(horas)) for h in horas]
    carga = [r for r in niveis.tabela(s, "carga") if r["semana"] == x["inicio"]]
    for r in carga:
        assert sem["carga"][r["submercado"]][pat == r["patamar"]].mean() == pytest.approx(r["mwmed"])
    # renováveis: a energia da semana das usinas do NE bate com a do equivalente do DECOMP
    eq = {}
    for r in niveis.tabela(s, "renovaveis"):
        if r["semana"] == x["inicio"] and r["submercado"] == "NE" and r["fonte"] == "EOL":
            eq[r["patamar"]] = eq.get(r["patamar"], 0) + r["mwmed"]
    cad = sims.casos_rede.cadastro("base")
    ids = [u["usina_id"] for u in cad.renovaveis(x["inicio"]) if u["fonte_energia"] == "EOL"]
    soma = sum(sem["renovaveis"][i] for i in ids)
    assert soma.sum() == pytest.approx(sum(v * (pat == p).sum() for p, v in eq.items()))
    # dia útil acima do domingo (a forma vem do histórico)
    dias = sem["dias"]
    util = next(i for i, d in enumerate(dias) if d == "2026-12-02")
    dom = next(i for i, d in enumerate(dias) if d == "2026-11-29")
    se = sem["carga"]["SE"]
    assert se[util * 48 : util * 48 + 48].mean() > se[dom * 48 : dom * 48 + 48].mean()


def test_cenarios_sorteados_mudam_e_ficam_perto_do_decomp(cli):
    sims = cli.app.state.simulacoes
    s = sims.ler("s1")
    x = niveis.semanas(s)[0]
    det = dia.semana(sims, s, x, 0)["carga"]["SE"].mean()
    cen = [dia.semana(sims, s, x, k)["carga"]["SE"].mean() for k in range(1, 6)]
    assert len(set(np.round(cen, 3))) > 1
    assert np.mean(cen) == pytest.approx(det, rel=0.05)


def test_exportar_dia(cli, tmp_path):
    sims = cli.app.state.simulacoes
    info = dia.exportar_dia(sims, "s1", "2026-12-02", 0, tmp_path / "dia")
    arqs = {p.name for p in (tmp_path / "dia").iterdir()}
    assert {"dia.json", "carga.parquet", "renovaveis.parquet", "termicas.parquet", "barras.parquet"} <= arqs
    assert len(info["patamar"]) == 48 and info["deck"] == "DC122026"
    assert info["avisos"]  # o deck mínimo não tem FCF
    assert json.loads((tmp_path / "dia" / "opcoes_deck.json").read_text())["operuh"] == "so_defluencia"
    carga = pq.read_table(tmp_path / "dia" / "carga.parquet")
    assert carga.num_rows == 48 * 5 * 3  # quarta a sexta
    assert len(info["dias"]) == 3
    assert json.loads((tmp_path / "dia" / "dia.json").read_text())["data"] == "2026-12-02"


def test_plano_de_execucao(cli):
    doc = cli.get("/api/simulacoes/s1/insumos/execucao").json()
    plano = doc["dados"]["plano"]
    assert [p["tipo"] for p in plano] == ["util", "sabado", "domingo"]
    assert sum(p["peso"] for p in plano) == 31
    doc["dados"]["opcoes"]["dias"] = "todos"
    doc["dados"]["opcoes"]["cenarios"] = 2
    cli.post("/api/simulacoes/s1/insumos/execucao", json=doc)
    doc = cli.get("/api/simulacoes/s1/insumos/execucao").json()
    assert doc["dados"]["resumo"] == {"dias": 31, "rodadas": 62}


def test_patamares():
    pat = series.patamares(np.arange(10.0), [2, 3, 5])
    assert pat.tolist() == [3, 3, 3, 3, 3, 2, 2, 2, 1, 1]


def test_capacidade_do_ons(cli, monkeypatch):
    sims = cli.app.state.simulacoes
    s = sims.ler("s1")
    x = niveis.semanas(s)[0]
    f = niveis.formas()
    monkeypatch.setattr(f, "capacidade", lambda uid: (1000.0, "ons") if uid == "U5441-1" else None, raising=False)
    sem = dia.semana(sims, s, x)
    assert sem["usinas"]["U5441-1"]["cap_mw"] == 1000.0 and sem["usinas"]["U5441-1"]["cap_origem"] == "ons"
    assert sem["usinas"]["U5441-2"]["cap_origem"] == "caso"


def test_dia_antes_da_foto_do_caso_base(cli, tmp_path):
    # a fixture é de 10/09/2026; um dia de backtest antes disso usa a rede da foto
    from kurt_app.dia import data_rede

    cad = cli.app.state.simulacoes.casos_rede.cadastro("base")
    assert data_rede(cad, "2026-01-05") == cad.data_base()
    assert data_rede(cad, "2026-12-05") == "2026-12-05"
    assert cad.renovaveis(data_rede(cad, "2026-01-05"))


def test_correcao_de_nivel_historica(cli, monkeypatch):
    sims = cli.app.state.simulacoes
    x = niveis.semanas(sims.ler("s1"))[0]
    f = niveis.formas()
    monkeypatch.setattr(f, "vies", lambda fonte, sub, mes, pat: 1.5 if fonte == "EOL" else 1.0, raising=False)
    monkeypatch.setattr(series, "FAIXA_FATOR", (0.0, 1e9))
    antes = dia.semana(sims, sims.ler("s1"), x)["fator_renovaveis"]["NE|EOL"]
    doc = cli.get("/api/simulacoes/s1/insumos/renovaveis").json()
    doc["dados"]["opcoes"]["correcao_nivel"] = "historica"
    assert cli.post("/api/simulacoes/s1/insumos/renovaveis", json=doc).status_code == 200
    depois = dia.semana(sims, sims.ler("s1"), x)["fator_renovaveis"]["NE|EOL"]
    assert depois == pytest.approx(1.5 * antes)


def test_sensibilidade_nivel_e_forma(cli):
    sims = cli.app.state.simulacoes
    x = niveis.semanas(sims.ler("s1"))[0]
    base = dia.semana(sims, sims.ler("s1"), x)
    doc = cli.get("/api/simulacoes/s1/insumos/renovaveis").json()
    pesos = [1.0] * 24
    for h in range(10, 15):
        pesos[h] = 3.0
    doc["dados"]["opcoes"]["sensibilidade"] = {
        "nivel": [{"fonte": "MMGD", "submercado": "SE", "fator": 1.2}],
        "forma": {"MMGD": pesos},
    }
    assert cli.post("/api/simulacoes/s1/insumos/renovaveis", json=doc).status_code == 200
    sem = dia.semana(sims, sims.ler("s1"), x)
    # nível: +20% de energia; forma: mais peso das 10 às 15 h, energia do dia mantida
    assert sem["mmgd"]["SE"].sum() == pytest.approx(1.2 * base["mmgd"]["SE"].sum())
    meio_dia = slice(20, 30)
    assert sem["mmgd"]["SE"][meio_dia].sum() / sem["mmgd"]["SE"][:48].sum() > (
        base["mmgd"]["SE"][meio_dia].sum() / base["mmgd"]["SE"][:48].sum()
    )
    # nenhuma usina passa da capacidade
    caps = {u: v["cap_mw"] for u, v in sem["usinas"].items()}
    assert all((sem["renovaveis"][u] <= caps[u] + 1e-9).all() for u in caps)


def test_sensibilidade_invalida_nao_grava(cli):
    antes = cli.get("/api/simulacoes/s1").json()
    doc = cli.get("/api/simulacoes/s1/insumos/renovaveis").json()
    doc["dados"]["opcoes"]["sensibilidade"] = {"forma": {"UFV": [1.0] * 23}}
    r = cli.post("/api/simulacoes/s1/insumos/renovaveis", json=doc)
    assert r.status_code == 422 and "24 pesos" in r.json()["detail"]
    doc["dados"]["opcoes"]["sensibilidade"] = {"nivel": [{"fonte": "EOL", "fator": 9}]}
    assert cli.post("/api/simulacoes/s1/insumos/renovaveis", json=doc).status_code == 422
    assert cli.get("/api/simulacoes/s1").json() == antes


def test_sensibilidade_da_carga_e_datas(cli):
    sims = cli.app.state.simulacoes
    x = niveis.semanas(sims.ler("s1"))[0]
    base = dia.semana(sims, sims.ler("s1"), x)
    d0 = base["dias"][0]
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    doc["dados"]["opcoes"]["sensibilidade"] = {
        "nivel": [
            {"fonte": "CARGA", "submercado": "NE", "fator": 1.1},
            {"fonte": "CARGA", "submercado": "todos", "fator": 0.5, "datas": [d0]},
        ],
    }
    assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc).status_code == 200
    sem = dia.semana(sims, sims.ler("s1"), x)
    ne, ne0 = sem["carga"]["NE"], base["carga"]["NE"]
    assert ne[:48].sum() == pytest.approx(0.55 * ne0[:48].sum())  # 1,1 × 0,5 no primeiro dia
    assert ne[48:].sum() == pytest.approx(1.1 * ne0[48:].sum())
    assert sem["carga"]["SE"][48:].sum() == pytest.approx(base["carga"]["SE"][48:].sum())
    # ida e volta estável
    doc2 = cli.get("/api/simulacoes/s1/insumos/carga").json()
    assert doc2["dados"]["opcoes"]["sensibilidade"] == doc["dados"]["opcoes"]["sensibilidade"] | {"forma": {}}
    assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc2).status_code == 200
    # fonte de renováveis na carga e datas inválidas não gravam
    antes = cli.get("/api/simulacoes/s1").json()
    for regra in (
        {"fonte": "EOL", "fator": 1.1},
        {"fonte": "CARGA", "fator": 1.1, "datas": ["2026-13-01"]},
        {"fonte": "CARGA", "fator": 1.1, "datas": [d0], "meses": [d0[:7]]},
    ):
        doc2["dados"]["opcoes"]["sensibilidade"] = {"nivel": [regra]}
        assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc2).status_code == 422
    assert cli.get("/api/simulacoes/s1").json() == antes


def test_ajuste_antigo_da_carga_vira_sensibilidade(cli):
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    doc["dados"]["opcoes"] = {"ajuste": {"SE": 1.02}}
    assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc).status_code == 200
    op = cli.get("/api/simulacoes/s1/insumos/carga").json()["dados"]["opcoes"]
    assert "ajuste" not in op
    assert op["sensibilidade"]["nivel"] == [{"fonte": "CARGA", "submercado": "SE", "fator": 1.02}]


def test_energia_por_dia_sem_sensibilidades(cli):
    sims = cli.app.state.simulacoes
    x = niveis.semanas(sims.ler("s1"))[0]
    r = cli.get("/api/simulacoes/s1/energia", params={"inicio": x["inicio"]})
    assert r.status_code == 200
    e = r.json()
    assert len(e["dias"]) == 7 and "CARGA SE" in e["base"] and "ANDE" in e["base"]
    sem = dia.semana(sims, sims.ler("s1"), x)
    assert e["base"]["CARGA SE"][0] == pytest.approx(sem["carga"]["SE"][:48].mean(), abs=0.1)
    # a sensibilidade não muda a base (a tela aplica os fatores por cima)
    doc = cli.get("/api/simulacoes/s1/insumos/carga").json()
    doc["dados"]["opcoes"]["sensibilidade"] = {"nivel": [{"fonte": "CARGA", "fator": 1.3}]}
    assert cli.post("/api/simulacoes/s1/insumos/carga", json=doc).status_code == 200
    assert cli.get("/api/simulacoes/s1/energia", params={"inicio": x["inicio"]}).json() == e
    assert cli.get("/api/simulacoes/s1/energia", params={"inicio": "1999-01-02"}).status_code == 422
