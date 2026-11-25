"""Fontes de dados (DECOMP, formas históricas, Myria), sem NAS e sem rede."""

import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kurt_app.fontes.decomp import DeckErro, ler_deck, resumir
from kurt_app.fontes.formas import Formas, datas, feriados, nivelar, pascoa, tipo_dia
from kurt_app.fontes.myria import Myria, MyriaErro

MINI = Path(__file__).resolve().parent.parent / "fixtures" / "decomp_mini"


# ---------------------------------------------------------------- DECOMP


def test_deck_decomp_mini():
    d = ler_deck(MINI)
    assert d.inicio == date(2026, 11, 28)
    est = d.estagios.to_pylist()
    assert [e["estagio"] for e in est] == [1, 6]
    assert est[0]["dias"] == 7.0 and est[0]["horas_pesada"] == 30.0
    carga = {(r["estagio"], r["submercado"], r["patamar"]): r["mwmed"] for r in d.carga.to_pylist()}
    assert carga[(1, "SE", 1)] == 54209.0 and carga[(6, "N", 3)] == 8242.0
    assert ("1", "FC") not in carga  # FC (fictício) não tem carga
    # CT do estágio 2 vale até o próximo registro: o estágio 6 (só no DP) herda dele
    ct = [r for r in d.termicas.to_pylist() if r["estagio"] == 6]
    assert {(r["inflex_mw"], r["disp_mw"], r["cvu"]) for r in ct} == {(213.0, 213.0, 31.17)}
    assert {r["estagio"] for r in d.termicas.to_pylist()} == {1, 6}
    pq_ = d.pequenas.to_pylist()[0]
    assert (pq_["submercado"], pq_["fonte"], pq_["mmgd"], pq_["mwmed"]) == ("SE", "UFV", True, 1173.0)
    ande = {(r["estagio"], r["patamar"]): r["mwmed"] for r in d.ande.to_pylist()}
    assert ande[(1, 1)] == 4193.0 and ande[(6, 3)] == 2787.0
    ren = d.renovaveis.to_pylist()
    assert ren[0]["fonte"] == "EOL" and ren[0]["submercado"] == "NE" and ren[0]["capacidade_mw"] is None
    assert next(r for r in ren if r["fonte"] == "UFV")["capacidade_mw"] == 1200.0
    assert d.volumes.to_pylist()[0] == {"usina": 1, "ree": 10, "vol_ini_pct": 47.8}
    vz = {(r["posto"], r["estagio"]): r["m3s"] for r in d.vazoes.to_pylist()}
    assert vz == {(1, 1): 10.0, (3, 1): 30.0, (1, 2): 11.0, (3, 2): 31.0}
    cen = d.vazoes_cenarios.to_pylist()
    assert {(r["cenario"], r["probabilidade"]) for r in cen} == {(1, 0.25), (2, 0.75)}
    assert d.fcf is None and any("cortdeco" in a for a in d.avisos)


def test_deck_zip_e_fcf(tmp_path):
    pasta = tmp_path / "deck"
    shutil.copytree(MINI, pasta)
    (pasta / "cortdeco.rv0").write_bytes(b"x")
    (pasta / "mapcut.rv0").write_bytes(b"y")
    assert set(ler_deck(pasta).fcf) == {"cortdeco", "mapcut"}
    z = shutil.make_archive(str(tmp_path / "deck"), "zip", pasta)
    assert ler_deck(z).carga.num_rows == ler_deck(pasta).carga.num_rows


def test_resumir(tmp_path):
    r = resumir(MINI, tmp_path / "saida")
    assert (tmp_path / "saida" / "carga.parquet").exists()
    assert json.loads((tmp_path / "saida" / "resumo.json").read_text())["linhas"]["carga"] == r["linhas"]["carga"]
    # média do estágio 1 no SE ponderada pelas horas: (54209*30 + 53230*74 + 44209*64)/168
    assert r["carga_mwmed_por_estagio_submercado"]["1|SE"] == pytest.approx(49968.2, abs=0.1)


def test_deck_sem_dadger(tmp_path):
    with pytest.raises(DeckErro):
        ler_deck(tmp_path)


# ---------------------------------------------------------------- formas


def test_calendario():
    assert pascoa(2026) == date(2026, 4, 5)
    assert date(2026, 2, 17) in feriados(2026)  # terça de carnaval
    assert date(2026, 11, 20) in feriados(2026) and date(2023, 11, 20) not in feriados(2023)
    assert tipo_dia(date(2026, 12, 25)) == "domingo"
    assert tipo_dia(date(2026, 12, 26)) == "sabado"
    assert tipo_dia(date(2026, 11, 20)) == "sabado"  # feriado em dia útil conta como sábado
    assert tipo_dia(date(2027, 1, 1)) == "domingo"
    assert tipo_dia(date(2026, 12, 23)) == "util"
    assert len(datas("2026-12")) == 31
    assert len(datas("2026-12", "domingo")) == 5  # 4 domingos + Natal


def test_formas_leitura(tmp_path):
    linhas = [
        {"submercado": "SE", "mes": 12, "tipo_dia": t, "meia_hora": mh, "fator": f + 0.01 * mh, "dias": 10}
        for t, f in (("util", 1.0), ("domingo", 0.8))
        for mh in range(48)
    ]
    pq.write_table(pa.Table.from_pylist(linhas), tmp_path / "carga.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"fonte": "EOL", "uf": u, "mes": 12, "hora": h, "fc": 0.4 if u == "BR" else 0.5}
                for u in ("RN", "BR")
                for h in range(24)
            ]
        ),
        tmp_path / "fc_grupos.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "usina_id": "EOL-1",
                    "id_ons": None,
                    "via": "grupo_fonte_uf",
                    "score": 0.0,
                    "cap_mw": 10.0,
                    "uf_grupo": "RN",
                    "fonte": "EOL",
                }
            ]
        ),
        tmp_path / "mapa_usinas.parquet",
    )
    f = Formas(tmp_path)
    p = f.perfil_carga("SE", 12, "domingo")
    assert p.shape == (48,) and p[0] == pytest.approx(0.8) and p[47] == pytest.approx(1.27)
    assert f.perfil_fc(12, usina_id="EOL-1").mean() == pytest.approx(0.5)
    assert f.perfil_fc(12, fonte="EOL", uf="SC").mean() == pytest.approx(0.4)  # UF sem histórico cai no país
    with pytest.raises(KeyError):
        f.perfil_carga("NE", 12, "util")
    assert nivelar(np.array([1.0, 3.0]), 100.0).tolist() == [50.0, 150.0]


# ---------------------------------------------------------------- Myria


class Falso:
    """Transporte simulado: responde por caminho e guarda as URLs pedidas."""

    def __init__(self, respostas):
        self.respostas, self.urls = respostas, []

    def __call__(self, url, cab):
        self.urls.append(url)
        assert cab["Authorization"].startswith("Bearer ")
        for trecho, (st, corpo) in self.respostas.items():
            if trecho in url:
                return st, corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
        return 404, b'{"error": {"code": "not_found"}}'


@pytest.fixture
def chave(tmp_path):
    p = tmp_path / "chave.key"
    p.write_text("myria_live_segredo.123\n")
    return p


def test_myria_paginacao_e_download(chave, tmp_path):
    t = Falso(
        {
            "cursor=c2": (200, {"data": [{"study_key": "teko-2"}], "page": {"has_more": False, "next_cursor": None}}),
            "/api/v1/studies?": (
                200,
                {"data": [{"study_key": "teko-1"}], "page": {"has_more": True, "next_cursor": "c2"}},
            ),
            "/artifacts?": (
                200,
                {
                    "data": [
                        {
                            "artifact_id": "art_1",
                            "execution_id": "exec_1",
                            "logical_name": "out/cortdeco.rv0",
                            "download_url": "/api/v1/executions/exec_1/artifacts/art_1/download",
                        }
                    ],
                    "page": {"has_more": False},
                },
            ),
            "/art_1/download": (200, b"\x00\x01FCF"),
        }
    )
    m = Myria(api="https://exemplo", arquivo_chave=chave, transporte=t)
    assert [e["study_key"] for e in m.listar_estudos()] == ["teko-1", "teko-2"]
    (a,) = m.artefatos("exec_1")
    p = m.baixar(a, tmp_path / "deck")
    assert p.name == "cortdeco.rv0" and p.read_bytes() == b"\x00\x01FCF"
    assert m.baixar(a, tmp_path / "deck") is None  # não sobrescreve
    assert all(u.startswith("https://exemplo/api/v1/") for u in t.urls)


def test_myria_erro_sem_vazar_chave(chave):
    m = Myria(api="https://exemplo", arquivo_chave=chave, transporte=Falso({}))
    with pytest.raises(MyriaErro) as e:
        m.estudo("teko-9")
    assert "404" in str(e.value) and "segredo" not in str(e.value)
    with pytest.raises(MyriaErro):
        Myria(api="https://exemplo", arquivo_chave=chave.parent / "nao-existe", transporte=Falso({})).estudo("x")


def test_formas_capacidade(tmp_path):
    base = {"via": "nome", "score": 1.0, "cap_mw": 10.0, "uf_grupo": "RN", "fonte": "EOL"}
    linhas = [
        {**base, "usina_id": "EOL-1", "id_ons": "X", "cap_ons_mw": 50.0, "cap_estimada_mw": 30.0, "cap_origem": "ons"},
        {
            **base,
            "usina_id": "EOL-2",
            "id_ons": None,
            "cap_ons_mw": None,
            "cap_estimada_mw": 30.0,
            "cap_origem": "decks_dessem",
        },
        {
            **base,
            "usina_id": "EOL-3",
            "id_ons": None,
            "cap_ons_mw": None,
            "cap_estimada_mw": None,
            "cap_origem": "caso",
        },
    ]
    pq.write_table(pa.Table.from_pylist(linhas), tmp_path / "mapa_usinas.parquet")
    f = Formas(tmp_path)
    assert f.capacidade("EOL-1") == (50.0, "ons")
    assert f.capacidade("EOL-2") == (30.0, "decks_dessem")
    assert f.capacidade("EOL-3") is None and f.capacidade("EOL-9") is None
