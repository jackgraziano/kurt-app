"""Gera as formas históricas (dados/formas/*.parquet) a partir do datalake no NAS.

Uso (na máquina Linux, com o NAS montado; leva alguns minutos):
    nice -n 10 .venv/bin/python scripts/formas_historico.py [--anos 3] [--saida dados/formas] \
        [--cadastro dados/cadastro/base-20261120]

Fontes (só leitura):
- carga: ons-carga-dessem/derived/api_cargaverificada.csv.gz (API de carga verificada do ONS,
  30 min, áreas SECO/S/NE/N; usa a carga global sem MMGD);
- MMGD: ons-geracao-renovavel/derived/mmgd_ons_subsistema.parquet (30 min, por submercado);
- fator de capacidade: public-ons/datasets/fator-capacidade-2 (horário, por usina ou conjunto
  eólico e solar, com geração verificada e capacidade instalada).

Saídas: ver kurt_app/fontes/formas.py.
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from kurt_app.expansao import nome_usina_deck, parecidos, score_codigo  # noqa: E402
from kurt_app.fontes.formas import SUBMERCADOS, TIPOS_DIA, tipo_dia  # noqa: E402

SERIES = Path("/mnt/nas/personal_folder/setor-eletrico/collections/series")
CARGA = SERIES / "ons-carga-dessem/derived/api_cargaverificada.csv.gz"
MMGD = SERIES / "ons-geracao-renovavel/derived/mmgd_ons_subsistema.parquet"
FC_GLOB = str(SERIES / "public-ons/datasets/fator-capacidade-2/originals/*/*/FATOR_CAPACIDADE-2_*.parquet")
AREA_SUB = {"SECO": "SE", "S": "S", "NE": "NE", "N": "N"}
FONTE_FC = {"Eólica": "EOL", "Solar": "UFV"}
FUSO = timedelta(hours=-3)  # Brasília, sem horário de verão desde 2019


def _f(s: str) -> float | None:
    try:
        return float(s) if s not in ("", None) else None
    except ValueError:
        return None


# ---------------------------------------------------------------- carga


def carga_diaria(inicio: date) -> dict[tuple[str, date], np.ndarray]:
    """(submercado, dia) -> 48 valores de carga sem MMGD (MW), só dias completos."""
    acc: dict[tuple[str, date], np.ndarray] = {}
    with gzip.open(CARGA, "rt", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            sub = AREA_SUB.get(r["cod_areacarga"])
            if sub is None or r["dat_referencia"] < inicio.isoformat():
                continue
            fim_utc = datetime.fromisoformat(r["din_referenciautc"].replace("Z", "+00:00")).replace(tzinfo=None)
            ini_local = fim_utc - timedelta(minutes=30) + FUSO
            v = _f(r["val_cargaglobalsmmgd"])
            if v is None:
                cons, mmgd = _f(r["val_cargaglobalcons"]), _f(r["val_cargammgd"]) or 0.0
                v = None if cons is None else cons - mmgd
            if v is None or v <= 0:
                continue
            k = (sub, ini_local.date())
            a = acc.get(k)
            if a is None:
                a = acc[k] = np.full(48, np.nan)
            a[ini_local.hour * 2 + ini_local.minute // 30] = v
    return {k: v for k, v in acc.items() if not np.isnan(v).any()}


def formas_carga(dias: dict[tuple[str, date], np.ndarray]) -> tuple[pa.Table, dict]:
    """Fator = carga / média do mês (do mesmo ano), média por (submercado, mês, tipo de dia)."""
    media_mes: dict[tuple[str, int, int], float] = {}
    soma: dict[tuple[str, int, int], list[float]] = defaultdict(lambda: [0.0, 0])
    for (sub, d), v in dias.items():
        s = soma[(sub, d.year, d.month)]
        s[0] += float(v.sum())
        s[1] += 48
    for k, (s, n) in soma.items():
        media_mes[k] = s / n
    grupos: dict[tuple[str, int, str], list[np.ndarray]] = defaultdict(list)
    norm = {}
    for (sub, d), v in dias.items():
        x = v / media_mes[(sub, d.year, d.month)]
        norm[(sub, d)] = x
        grupos[(sub, d.month, tipo_dia(d))].append(x)
    linhas = []
    for sub in SUBMERCADOS:
        for mes in range(1, 13):
            # reescala para que a média ponderada pelos dias de cada tipo seja 1
            perf = {t: np.mean(grupos[(sub, mes, t)], axis=0) for t in TIPOS_DIA if grupos.get((sub, mes, t))}
            if not perf:
                continue
            n = {t: len(grupos[(sub, mes, t)]) for t in perf}
            m = sum(perf[t].mean() * n[t] for t in perf) / sum(n.values())
            for t, p in perf.items():
                for mh in range(48):
                    linhas.append(
                        {
                            "submercado": sub,
                            "mes": mes,
                            "tipo_dia": t,
                            "meia_hora": mh,
                            "fator": float(p[mh] / m),
                            "dias": n[t],
                        }
                    )
    return pa.Table.from_pylist(linhas), norm


# ---------------------------------------------------------------- MMGD


def formas_mmgd(inicio: date) -> pa.Table:
    t = pq.read_table(MMGD)
    inst = t["instante"].to_pylist()
    linhas = []
    for sub in SUBMERCADOS:
        v = t[sub].to_numpy(zero_copy_only=False)
        acc: dict[tuple[int, int], list[float]] = defaultdict(list)
        for i, ts in enumerate(inst):
            if ts.date() < inicio or v[i] is None or np.isnan(v[i]):
                continue
            acc[(ts.month, ts.hour * 2 + ts.minute // 30)].append(float(v[i]))
        for mes in range(1, 13):
            vals = np.array([np.mean(acc[(mes, mh)]) if acc.get((mes, mh)) else np.nan for mh in range(48)])
            if np.isnan(vals).all():
                continue
            vals = vals / np.nanmean(vals)
            for mh in range(48):
                linhas.append({"submercado": sub, "fonte": "UFV", "mes": mes, "meia_hora": mh, "fc": float(vals[mh])})
    return pa.Table.from_pylist(linhas)


# ---------------------------------------------------------------- fator de capacidade


def ler_fc(inicio: date) -> pa.Table:
    cols = [
        "id_subsistema",
        "id_estado",
        "nom_tipousina",
        "nom_usina_conjunto",
        "id_ons",
        "ceg",
        "nom_pontoconexao",
        "din_instante",
        "val_geracaoverificada",
        "val_capacidadeinstalada",
        "val_fatorcapacidade",
    ]
    partes = []
    for arq in sorted(glob.glob(FC_GLOB)):
        ano_mes = arq.rsplit("_", 2)[-2:]
        try:
            a, m = int(ano_mes[0]), int(ano_mes[1].split(".")[0])
        except ValueError:
            continue
        if date(a, m, 1) < inicio.replace(day=1):
            continue
        t = pq.read_table(arq, columns=cols)
        t = t.set_column(
            t.schema.get_field_index("din_instante"), "din_instante", pc.cast(t["din_instante"], pa.timestamp("s"))
        )
        for c in ("val_geracaoverificada", "val_capacidadeinstalada", "val_fatorcapacidade"):
            col = t[c]
            if pa.types.is_string(col.type):
                col = pc.replace_substring(col, ",", ".")
            if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
                col = pc.if_else(pc.equal(pc.utf8_trim_whitespace(col), ""), pa.scalar(None, col.type), col)
            t = t.set_column(t.schema.get_field_index(c), c, pc.cast(col, pa.float64()))
        partes.append(t)
    t = pa.concat_tables(partes)
    fonte = pc.replace_substring(pc.replace_substring(t["nom_tipousina"], "Eólica", "EOL"), "Solar", "UFV")
    t = t.append_column("fonte", fonte)
    t = t.append_column("mes", pc.month(t["din_instante"]))
    t = t.append_column("hora", pc.hour(t["din_instante"]))
    t = t.append_column("dia", pc.cast(t["din_instante"], pa.date32()))
    fc = pc.if_else(pc.greater(t["val_fatorcapacidade"], 1.5), None, t["val_fatorcapacidade"])
    return t.append_column("fc", fc)


def formas_fc(t: pa.Table) -> tuple[pa.Table, pa.Table, pa.Table]:
    g = (
        t.group_by(["id_ons", "mes", "hora"])
        .aggregate([("fc", "mean")])
        .rename_columns(["id_ons", "mes", "hora", "fc"])
    )
    atr = t.group_by(["id_ons"]).aggregate(
        [
            ("nom_usina_conjunto", "max"),
            ("ceg", "max"),
            ("fonte", "max"),
            ("id_estado", "max"),
            ("id_subsistema", "max"),
            ("nom_pontoconexao", "max"),
            ("val_capacidadeinstalada", "max"),
        ]
    )
    atr = atr.rename_columns(["id_ons", "nome", "ceg", "fonte", "uf", "submercado", "ponto_conexao", "cap_mw"])
    usinas = g.join(atr.select(["id_ons", "nome", "ceg", "fonte", "uf", "submercado"]), "id_ons")
    ok = pc.and_(pc.is_valid(t["val_geracaoverificada"]), pc.greater(t["val_capacidadeinstalada"], 0))
    tg = t.filter(ok)
    gr = tg.group_by(["fonte", "id_estado", "mes", "hora"]).aggregate(
        [("val_geracaoverificada", "sum"), ("val_capacidadeinstalada", "sum")]
    )
    grupos = pa.table(
        {
            "fonte": gr["fonte"],
            "uf": gr["id_estado"],
            "mes": gr["mes"],
            "hora": gr["hora"],
            "fc": pc.divide(gr["val_geracaoverificada_sum"], gr["val_capacidadeinstalada_sum"]),
        }
    )
    # "BR": a fonte no país inteiro, último recurso para UF sem histórico
    br = tg.group_by(["fonte", "mes", "hora"]).aggregate(
        [("val_geracaoverificada", "sum"), ("val_capacidadeinstalada", "sum")]
    )
    br_t = pa.table(
        {
            "fonte": br["fonte"],
            "uf": pa.array(["BR"] * br.num_rows),
            "mes": br["mes"],
            "hora": br["hora"],
            "fc": pc.divide(br["val_geracaoverificada_sum"], br["val_capacidadeinstalada_sum"]),
        }
    )
    grupos = pa.concat_tables([grupos, br_t.cast(grupos.schema)])
    return usinas, grupos, atr


def fc_diario(t: pa.Table) -> dict[tuple[str, str, date], np.ndarray]:
    """(fonte, submercado, dia) -> 24 FCs (geração / capacidade somadas)."""
    ok = pc.and_(pc.is_valid(t["val_geracaoverificada"]), pc.greater(t["val_capacidadeinstalada"], 0))
    tg = t.filter(ok)
    gr = tg.group_by(["fonte", "id_subsistema", "dia", "hora"]).aggregate(
        [("val_geracaoverificada", "sum"), ("val_capacidadeinstalada", "sum")]
    )
    out: dict[tuple[str, str, date], np.ndarray] = {}
    f, s, d, h = (gr[c].to_pylist() for c in ("fonte", "id_subsistema", "dia", "hora"))
    ger = gr["val_geracaoverificada_sum"].to_numpy()
    cap = gr["val_capacidadeinstalada_sum"].to_numpy()
    for i in range(gr.num_rows):
        k = (f[i], s[i], d[i])
        a = out.get(k)
        if a is None:
            a = out[k] = np.full(24, np.nan)
        a[h[i]] = ger[i] / cap[i]
    return {k: v for k, v in out.items() if not np.isnan(v).any()}


# ---------------------------------------------------------------- casamento com o caso base


def mapear_usinas(cadastro: Path, atr: pa.Table) -> tuple[pa.Table, dict]:
    us = [r for r in pq.read_table(cadastro / "usinas.parquet").to_pylist() if r["fonte_energia"] in ("EOL", "UFV")]
    barras = {b["barra_id"]: b for b in pq.read_table(cadastro / "barras.parquet").to_pylist()}
    ons = atr.to_pylist()
    por_ponto: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in ons:
        por_ponto[(o["fonte"], o["uf"] or "")].append(o)
    linhas = []
    for u in us:
        b = barras.get(u["barra_id"], {})
        uf, sub, fonte = b.get("uf"), u.get("submercado"), u["fonte_energia"]
        nome = nome_usina_deck(u["nome"])
        cand = [o for o in ons if o["fonte"] == fonte and (o["submercado"] == sub or sub is None)]
        best = max(
            ((parecidos(nome, (o["nome"] or "").replace("Conj.", "")), o) for o in cand),
            key=lambda x: x[0],
            default=(0.0, None),
        )
        if best[1] is not None and best[0] >= 0.8:
            linhas.append(
                {
                    "usina_id": u["usina_id"],
                    "id_ons": best[1]["id_ons"],
                    "via": "nome",
                    "score": round(best[0], 3),
                    "cap_mw": u["cap_mw"],
                }
            )
            continue
        # mesmo ponto de conexão: os nomes do deck costumam ser "CJF <subestação> 500kV"; compara com o
        # ponto de conexão do ONS (no submercado) e, se a barra tiver UF, com o código da subestação dela
        sub_b = b.get("subestacao") or ""
        nome_pt = " ".join(w for w in nome.split() if w.upper() not in ("CJE", "CJF", "CJ") and "KV" not in w.upper())
        pts = [(parecidos(nome_pt, o["ponto_conexao"] or ""), o) for o in cand]
        if uf:
            pts += [(score_codigo(o["ponto_conexao"] or "", sub_b), o) for o in por_ponto.get((fonte, uf), [])]
        pts = [x for x in pts if x[0] >= 0.8]
        if pts:
            sc = max(x[0] for x in pts)
            o = max((x[1] for x in pts if x[0] == sc), key=lambda o: o["cap_mw"] or 0)
            linhas.append(
                {
                    "usina_id": u["usina_id"],
                    "id_ons": o["id_ons"],
                    "via": "ponto_conexao",
                    "score": round(sc, 3),
                    "cap_mw": u["cap_mw"],
                }
            )
            continue
        linhas.append(
            {"usina_id": u["usina_id"], "id_ons": None, "via": "grupo_fonte_uf", "score": 0.0, "cap_mw": u["cap_mw"]}
        )
    uf_maior: dict[tuple[str, str], str] = {}
    cap_uf: dict[tuple[str, str, str], float] = defaultdict(float)
    for o in ons:
        cap_uf[(o["fonte"], o["submercado"], o["uf"])] += o["cap_mw"] or 0
    for (f, s_, uf_), _c in sorted(cap_uf.items(), key=lambda x: x[1]):
        uf_maior[(f, s_)] = uf_
    por_id = {u["usina_id"]: u for u in us}
    tem_grupo = {(o["fonte"], o["uf"]) for o in ons}
    cap_pais: dict[tuple[str, str], float] = defaultdict(float)
    for (f, _s, uf_), c in cap_uf.items():
        cap_pais[(f, uf_)] += c
    uf_pais: dict[str, str] = {}
    for (f, uf_), _c in sorted(cap_pais.items(), key=lambda x: x[1]):
        uf_pais[f] = uf_
    for r in linhas:
        u = por_id[r["usina_id"]]
        uf = barras.get(u["barra_id"], {}).get("uf")
        f_ = u["fonte_energia"]
        # a UF da barra só serve se houver perfil da fonte nela; senão, a UF de maior capacidade
        # da fonte no submercado; senão, a do país
        r["uf_grupo"] = uf if (f_, uf) in tem_grupo else uf_maior.get((f_, u.get("submercado"))) or uf_pais.get(f_)
        r["fonte"] = u["fonte_energia"]
    tot = len(linhas)
    cap = sum(r["cap_mw"] or 0 for r in linhas)
    taxa = {}
    for via in ("nome", "ponto_conexao", "grupo_fonte_uf"):
        rs = [r for r in linhas if r["via"] == via]
        taxa[via] = {
            "usinas": len(rs),
            "pct_usinas": round(100 * len(rs) / tot, 1) if tot else 0,
            "pct_mw": round(100 * sum(r["cap_mw"] or 0 for r in rs) / cap, 1) if cap else 0,
        }
    return pa.Table.from_pylist(linhas), taxa


# ---------------------------------------------------------------- dias para sorteio conjunto


def tabela_dias(norm_carga: dict, fc_dia: dict) -> pa.Table:
    datas_ = sorted({d for (_, d) in norm_carga})
    combos = sorted({(f, s) for (f, s, _) in fc_dia})
    cols: dict[str, list] = {"data": [], "mes": [], "tipo_dia": []}
    for s in SUBMERCADOS:
        cols[f"carga_{s}"] = []
    for f, s in combos:
        cols[f"fc_{f}_{s}"] = []
    for d in datas_:
        if not all((s, d) in norm_carga for s in SUBMERCADOS):
            continue
        cols["data"].append(d)
        cols["mes"].append(d.month)
        cols["tipo_dia"].append(tipo_dia(d))
        for s in SUBMERCADOS:
            cols[f"carga_{s}"].append(norm_carga[(s, d)].astype("float32").tolist())
        for f, s in combos:
            v = fc_dia.get((f, s, d))
            cols[f"fc_{f}_{s}"].append(None if v is None else np.repeat(v, 2).astype("float32").tolist())
    return pa.table(cols)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--anos", type=int, default=3)
    p.add_argument("--saida", default=str(RAIZ / "dados" / "formas"))
    p.add_argument("--cadastro", default=str(RAIZ / "dados" / "cadastro" / "base-20261120"))
    p.add_argument("--fim", default=None, help="último dia do histórico (padrão: o mais recente disponível)")
    a = p.parse_args()
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    fim = date.fromisoformat(a.fim) if a.fim else date.today()
    inicio = date(fim.year - a.anos, fim.month, 1)
    rel: dict = {"inicio": inicio.isoformat(), "gerado_em": datetime.now().isoformat(timespec="seconds")}

    print("carga...", flush=True)
    dias_c = carga_diaria(inicio)
    t_carga, norm = formas_carga(dias_c)
    pq.write_table(t_carga, saida / "carga.parquet")
    rel["carga"] = {
        "dias": len(dias_c),
        "linhas": t_carga.num_rows,
        "ultimo_dia": max(d for (_, d) in dias_c).isoformat(),
    }

    print("mmgd...", flush=True)
    t_mmgd = formas_mmgd(inicio)
    pq.write_table(t_mmgd, saida / "mmgd.parquet")
    rel["mmgd"] = {"linhas": t_mmgd.num_rows}

    print("fator de capacidade...", flush=True)
    t_fc = ler_fc(inicio)
    usinas, grupos, atr = formas_fc(t_fc)
    pq.write_table(usinas, saida / "fc_usinas.parquet")
    pq.write_table(grupos, saida / "fc_grupos.parquet")
    rel["fc"] = {"linhas_horarias": t_fc.num_rows, "usinas_ons": atr.num_rows, "grupos": grupos.num_rows}

    print("casamento com o caso base...", flush=True)
    mapa, taxa = mapear_usinas(Path(a.cadastro), atr)
    pq.write_table(mapa, saida / "mapa_usinas.parquet")
    rel["casamento"] = taxa

    print("dias...", flush=True)
    t_dias = tabela_dias(norm, fc_diario(t_fc))
    pq.write_table(t_dias, saida / "dias.parquet")
    rel["dias"] = {"linhas": t_dias.num_rows, "colunas": t_dias.column_names}

    (saida / "relatorio.json").write_text(json.dumps(rel, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(rel, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
