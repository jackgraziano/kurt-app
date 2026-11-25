"""Viés do nível das renováveis do DECOMP contra a previsão do DESSEM (dados/formas/vies_renovaveis.parquet).

Uso (na máquina Linux, com o NAS montado; leva alguns minutos):
    nice -n 10 .venv/bin/python scripts/vies_decomp_dessem.py [--meses 18] [--fim 2026-11-13] \
        [--excluir 2026-11-14:2026-11-20] [--saida dados/formas] [--processos 4]

Para cada semana operativa com deck DECOMP oficial do ONS (valor esperado) e decks DESSEM no NAS:
- nível do DECOMP: estágio 1 do deck da revisão que cobre o dia (renovaveis.csv para EOL e UFV, PQ com "gd"
  para a MMGD), por fonte, submercado e patamar;
- previsão do DESSEM: primeiro dia de cada deck diário da semana (meias-horas), somada por fonte e submercado
  (nomes terminados em _UEE, _UFV e _MGD/_MMGD_), com o patamar do registro TM do próprio deck;
- razão da semana = média do DESSEM nas meias-horas do patamar / nível do DECOMP no patamar. O patamar 0 é o
  dia inteiro (média do DESSEM / nível do DECOMP ponderado pelas meias-horas de cada patamar).

A razão gravada por (fonte, submercado, mês do ano, patamar) é a média das razões semanais; fora de [0,5; 2] ela é
diferença de cadastro entre os modelos, não viés, e fica 1 (`razao_bruta` guarda a média); `n_semanas` conta quantas entraram. O nível de EOL e UFV vem do
renovaveis.csv quando o deck o tem e, nos decks anteriores (até 10/2026), do PQ sem "gd". Semanas que tocam o intervalo de --excluir ficam de fora (o backtest não pode ver o futuro).

Saídas em --saida: vies_renovaveis.parquet (fonte, submercado, mes, patamar, razao, razao_bruta, n_semanas) e
vies_renovaveis.json (semanas usadas, excluídas e o detalhe por semana).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from kurt_app.fontes.decomp import ler_deck  # noqa: E402

DECOMP = Path("/mnt/nas/personal_folder/Decks/data/raw/ons/decomp/valor-esperado")
DESSEM = Path("/mnt/nas/personal_folder/Decks/data/raw/ons/dessem/entrada-saida")
FONTES = ("EOL", "UFV", "MMGD")
SUBS = ("SE", "S", "NE", "N")
NOME_PAT = {"PESADA": 1, "MEDIA": 2, "LEVE": 3}
RAZAO_MIN, RAZAO_MAX = 0.5, 2.0
RE_DEC = re.compile(r"(\d{8})__DEC_ONS_(\d{2})(\d{4})_RV(\d)_VE\.zip$", re.I)
RE_DES = re.compile(r"(\d{8})__ds_ons_(\d{2})(\d{4})_rv(\d)d(\d{2})", re.I)


def _data(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def indice_decomp() -> dict[tuple[int, int, int], tuple[date, Path]]:
    out = {}
    for z in DECOMP.glob("*/*.zip"):
        m = RE_DEC.search(z.name)
        if m:
            out[(int(m[3]), int(m[2]), int(m[4]))] = (_data(m[1]), z)
    return out


def indice_dessem() -> dict[tuple[int, int, int], list[tuple[date, Path]]]:
    out: dict[tuple[int, int, int], list[tuple[date, Path]]] = defaultdict(list)
    for z in DESSEM.glob("*/*.zip"):
        m = RE_DES.search(z.name)
        if m:
            out[(int(m[3]), int(m[2]), int(m[4]))].append((_data(m[1]), z))
    return out


def fonte_de(nome: str) -> str | None:
    n = nome.strip()
    if "_MMGD_" in n or n.endswith("_MGD"):
        return "MMGD"
    if n.endswith("_UEE"):
        return "EOL"
    if n.endswith("_UFV"):
        return "UFV"
    return None


def dia_dessem(arq: str) -> dict | None:
    """Primeiro dia do deck: {(fonte, sub, patamar): soma MW nas meias-horas} e meias-horas por patamar."""
    try:
        z = zipfile.ZipFile(arq)
        nomes = {n.lower(): n for n in z.namelist()}
        ent = z.read(nomes["entdados.dat"]).decode("latin-1")
        ren = z.read(nomes["renovaveis.dat"]).decode("latin-1")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    tm = [ln for ln in ent.splitlines() if ln.startswith("TM")]
    if len(tm) < 48:
        return None
    d0 = int(tm[0][4:6])
    pat = []
    for ln in tm[:48]:
        p = NOME_PAT.get(ln.split()[-1].upper())
        if p is None:
            return None
        pat.append(p)
    fonte, sub = {}, {}
    serie: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0] * 48)
    for ln in ren.splitlines():
        if ln.startswith("EOLICA ;"):
            c = ln.split(";")
            fonte[c[1].strip()] = fonte_de(c[2])
        elif ln.startswith("EOLICASUBM"):
            c = ln.split(";")
            sub[c[1].strip()] = c[2].strip()
    for ln in ren.splitlines():
        if not ln.startswith("EOLICA-GERACAO"):
            continue
        c = [x.strip() for x in ln.split(";")]
        f, s = fonte.get(c[1]), sub.get(c[1])
        if f is None or s not in SUBS:
            continue
        try:
            di, hi, mi = int(c[2]), int(c[3]), int(c[4])
            v = float(c[8])
        except (IndexError, ValueError):
            continue
        if di != d0:
            continue
        a = hi * 2 + mi
        b = 48 if c[5] in ("F", "") or int(c[5]) != d0 else int(c[6]) * 2 + int(c[7] or 0)
        for i in range(a, min(max(b, a + 1), 48)):
            serie[(f, s)][i] += v
    soma = defaultdict(float)
    for (f, s), v in serie.items():
        for i in range(48):
            soma[(f, s, pat[i])] += v[i]
    n_pat = defaultdict(int)
    for p in pat:
        n_pat[p] += 1
    return {"soma": {f"{f}|{s}|{p}": x for (f, s, p), x in soma.items()}, "n": dict(n_pat)}


def nivel_decomp(arq: Path) -> dict[tuple[str, str, int], float]:
    d = ler_deck(arq)
    out: dict[tuple[str, str, int], float] = defaultdict(float)
    ren = [r for r in d.renovaveis.to_pylist() if r["estagio"] == 1 and r["fonte"] in ("EOL", "UFV")]
    for r in ren:
        if r["submercado"] in SUBS:
            out[(r["fonte"], r["submercado"], r["patamar"])] += r["mwmed"] or 0.0
    for r in d.pequenas.to_pylist():
        if r["estagio"] != 1 or r["submercado"] not in SUBS:
            continue
        if r["mmgd"]:
            out[("MMGD", r["submercado"], r["patamar"])] += r["mwmed"] or 0.0
        elif not ren and r["fonte"] in ("EOL", "UFV"):
            # decks anteriores ao renovaveis.csv (até 10/2026) trazem eólica e solar no PQ
            out[(r["fonte"], r["submercado"], r["patamar"])] += r["mwmed"] or 0.0
    return dict(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--meses", type=int, default=18)
    p.add_argument("--fim", default="2026-11-13", help="último dia de DESSEM usado")
    p.add_argument("--excluir", default="2026-11-14:2026-11-20", help="intervalo de datas fora do cálculo")
    p.add_argument("--saida", default=str(RAIZ / "dados" / "formas"))
    p.add_argument("--processos", type=int, default=4)
    a = p.parse_args()
    fim = date.fromisoformat(a.fim)
    inicio = fim - timedelta(days=round(a.meses * 30.44))
    ex_a, ex_b = (date.fromisoformat(x) for x in a.excluir.split(":"))

    dec = indice_decomp()
    des = indice_dessem()
    semanas = []
    excluidas = []
    for chave, (d_ini, z) in sorted(dec.items(), key=lambda kv: kv[1][0]):
        d_fim = d_ini + timedelta(days=6)
        if d_ini < inicio or d_ini > fim:
            continue
        if d_ini <= ex_b and d_fim >= ex_a:
            excluidas.append(z.name)
            continue
        dias = [(d, zz) for d, zz in des.get(chave, []) if d_ini <= d <= min(d_fim, fim)]
        if dias:
            semanas.append((chave, d_ini, z, sorted(dias)))
    print(f"{len(semanas)} semanas de {inicio} a {fim}; excluídas: {excluidas}", flush=True)

    todos = [str(zz) for _, _, _, dias in semanas for _, zz in dias]
    with ProcessPoolExecutor(a.processos) as ex:
        res_dia = dict(zip(todos, ex.map(dia_dessem, todos, chunksize=4), strict=True))

    detalhe = []
    acc: dict[tuple[str, str, int, int], list[float]] = defaultdict(list)
    for _chave, d_ini, z, dias in semanas:
        try:
            niv = nivel_decomp(z)
        except Exception as e:  # deck antigo ou corrompido: registra e segue
            detalhe.append({"decomp": z.name, "erro": str(e)[:200]})
            continue
        soma: dict[tuple[str, str, int], float] = defaultdict(float)
        n_pat: dict[int, int] = defaultdict(int)
        n_dias = 0
        for _, zz in dias:
            r = res_dia.get(str(zz))
            if not r:
                continue
            n_dias += 1
            for k, v in r["soma"].items():
                f, s, pt = k.split("|")
                soma[(f, s, int(pt))] += v
            for pt, n in r["n"].items():
                n_pat[int(pt)] += n
        if not n_dias:
            continue
        sem = {"decomp": z.name, "inicio": d_ini.isoformat(), "dias_dessem": n_dias, "razoes": {}}
        for f in FONTES:
            for s in SUBS:
                num_d, den_d = 0.0, 0.0
                for pt in (1, 2, 3):
                    n = n_pat.get(pt, 0)
                    lv = niv.get((f, s, pt))
                    if not n or lv is None:
                        continue
                    md = soma.get((f, s, pt), 0.0) / n
                    num_d += soma.get((f, s, pt), 0.0)
                    den_d += lv * n
                    if lv > 1.0:
                        r = md / lv
                        acc[(f, s, d_ini.month, pt)].append(r)
                        sem["razoes"][f"{f}|{s}|{pt}"] = round(r, 4)
                if den_d > 48.0:
                    r = num_d / den_d
                    acc[(f, s, d_ini.month, 0)].append(r)
                    sem["razoes"][f"{f}|{s}|0"] = round(r, 4)
        detalhe.append(sem)
        print(f"{z.name}: {n_dias} dias DESSEM", flush=True)

    # razão fora de [0,5; 2] é diferença de cadastro entre os dois modelos (ex.: eólica do SE com 110 MW no DECOMP e
    # ~1,3 GW no DESSEM; solar do S quase toda como MMGD no DESSEM), não viés de previsão: grava 1 (sem correção) e
    # deixa a média em razao_bruta
    linhas = [
        {
            "fonte": f,
            "submercado": s,
            "mes": m,
            "patamar": pt,
            "razao": (sum(v) / len(v)) if RAZAO_MIN <= sum(v) / len(v) <= RAZAO_MAX else 1.0,
            "razao_bruta": sum(v) / len(v),
            "n_semanas": len(v),
        }
        for (f, s, m, pt), v in sorted(acc.items())
    ]
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    tab = pa.Table.from_pylist(
        linhas,
        schema=pa.schema(
            [
                ("fonte", pa.string()),
                ("submercado", pa.string()),
                ("mes", pa.int32()),
                ("patamar", pa.int32()),
                ("razao", pa.float64()),
                ("razao_bruta", pa.float64()),
                ("n_semanas", pa.int32()),
            ]
        ),
    )
    pq.write_table(tab, saida / "vies_renovaveis.parquet")
    rel = {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "excluir": a.excluir,
        "semanas_excluidas": excluidas,
        "semanas": len([d for d in detalhe if "razoes" in d]),
        "detalhe": detalhe,
    }
    (saida / "vies_renovaveis.json").write_text(json.dumps(rel, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(linhas)} linhas em {saida / 'vies_renovaveis.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
