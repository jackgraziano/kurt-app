"""Cenário de expansão de geração a partir do RALIE da ANEEL.

Uso:
    .venv/bin/python scripts/expansao_ralie.py --cadastro dados/cadastro/base-20261120 \
        [--cenario expansao] [--data 2026-11-24] [--sem-download] [--recriar]

1. Baixa ralie-usina-atual.csv e ralie-unidade-geradora-atual.csv para
   dados/fontes/aneel/<data>/ (pula se já estiverem lá ou com --sem-download).
2. Casa cada usina do SIN com uma barra do caso base (kurt_app.expansao).
3. Grava em dados/cenarios/<cadastro>/: expansao_arquivos/expansao_eventos.json e
   expansao_resumo.json, expansao_pendencias.csv
   (sem casamento confiável, com os 3 melhores candidatos), expansao_no_deck.csv
   (possivelmente já no deck) e expansao_casadas.csv.
4. Cria o cenário (ou recria, com --recriar) e importa os eventos.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from kurt_app.cadastro import Cadastro  # noqa: E402
from kurt_app.cenario import Cenarios  # noqa: E402
from kurt_app.expansao import montar  # noqa: E402

PACOTE = "https://dadosabertos.aneel.gov.br/dataset/57e4b8b5-a5db-40e6-9901-27ca629d0477/resource"
ARQUIVOS = {
    "ralie-usina-atual.csv": f"{PACOTE}/4a615df8-4c25-48fa-bbea-873a36a79518/download/ralie-usina-atual.csv",
    "ralie-unidade-geradora-atual.csv": f"{PACOTE}/8a794ee4-3d41-4ce7-a80a-ae7702a16b7b/download/ralie-unidade-geradora-atual.csv",
}
DESCRICAO = "Expansão oficial: ANEEL RALIE + obras de transmissão"


def baixar(pasta: Path, sem_download: bool) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, url in ARQUIVOS.items():
        alvo = pasta / nome
        if alvo.exists() and alvo.stat().st_size > 0:
            continue
        if sem_download:
            raise SystemExit(f"{alvo} não existe e --sem-download foi pedido")
        print(f"baixando {nome}")
        req = urllib.request.Request(url, headers={"User-Agent": "kurt-app"})
        with urllib.request.urlopen(req, timeout=300) as r:
            alvo.write_bytes(r.read())


def ler_csv(arq: Path) -> list[dict]:
    with arq.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def gravar_csv(arq: Path, linhas: list[dict], colunas: list[str]) -> None:
    with arq.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas, extrasaction="ignore")
        w.writeheader()
        for r in linhas:
            w.writerow(r)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cadastro", required=True)
    p.add_argument("--cenario", default="expansao")
    p.add_argument("--data", default=date.today().isoformat(), help="pasta dos CSV em dados/fontes/aneel/")
    p.add_argument("--sem-download", action="store_true")
    p.add_argument("--recriar", action="store_true", help="apaga o cenário e os filhos antes de importar")
    p.add_argument("--horizonte", default="2031-12-31")
    a = p.parse_args()

    fontes = RAIZ / "dados" / "fontes" / "aneel" / a.data
    baixar(fontes, a.sem_download)
    usinas = ler_csv(fontes / "ralie-usina-atual.csv")
    unidades = ler_csv(fontes / "ralie-unidade-geradora-atual.csv")
    data_ralie = max((u.get("DatRalie") or "")[:10] for u in usinas) or a.data

    pasta_cad = Path(a.cadastro)
    base = {t: pq.read_table(pasta_cad / f"{t}.parquet").to_pylist() for t in ("barras", "usinas")}
    cad = Cadastro(pasta_cad)
    data_base = date.fromisoformat(cad.data_base())
    r = montar(usinas, unidades, base, data_base, f"ANEEL RALIE {data_ralie}", date.fromisoformat(a.horizonte))

    saida = RAIZ / "dados" / "cenarios" / pasta_cad.resolve().name
    saida.mkdir(parents=True, exist_ok=True)
    aux = saida / "expansao_arquivos"  # JSON fora da pasta dos cenários (que lê todo *.json)
    aux.mkdir(exist_ok=True)
    (aux / "expansao_eventos.json").write_text(json.dumps(r["eventos"], ensure_ascii=False, indent=1) + "\n")
    for p_ in r["pendencias"]:
        for i in range(3):
            c = p_["candidatos"][i] if i < len(p_["candidatos"]) else ("", "")
            p_[f"candidato{i + 1}"], p_[f"score{i + 1}"] = c
    cols = [
        "CodCEG",
        "nome",
        "complexo",
        "fonte",
        "uf",
        "conexao",
        "tensao_kv",
        "mw_ate_horizonte",
        "primeira_data",
        "situacao_obra",
        "motivo",
    ]
    gravar_csv(
        saida / "expansao_pendencias.csv",
        r["pendencias"],
        [*cols, "candidato1", "score1", "candidato2", "score2", "candidato3", "score3"],
    )
    gravar_csv(saida / "expansao_no_deck.csv", r["no_deck"], [*cols, "usina_deck", "nome_deck", "semelhanca"])
    gravar_csv(
        saida / "expansao_casadas.csv",
        r["casadas"],
        [*cols, "via", "score", "subestacao", "barra_id", "submercado", "usina_id"],
    )

    cens = Cenarios(cad, saida)
    existe = any(c["nome"] == a.cenario for c in cens.listar())
    if existe and a.recriar:
        for c in cens.listar():
            if c.get("pai") == a.cenario:
                cens.apagar(c["nome"])
        cens.apagar(a.cenario)
        existe = False
    if not existe:
        cens.criar(a.cenario, DESCRICAO, None, a.horizonte)
    prontos = cens.adicionar_eventos(a.cenario, r["eventos"])

    # resumo: GW que entram por ano x fonte x submercado (potência acumulada no fim de cada ano)
    gw: dict[tuple, float] = defaultdict(float)
    ultimo: dict[str, tuple[str, float]] = {}
    info = {c["usina_id"]: c for c in r["casadas"]}
    for ev in sorted(r["eventos"], key=lambda e: e["data"]):
        cap = ev["registro"]["cap_mw"] if ev["acao"] == "entra" else ev["campos"]["cap_mw"]
        antes = ultimo.get(ev["entidade_id"], ("", 0.0))[1]
        c = info[ev["entidade_id"]]
        gw[(ev["data"][:4], c["fonte"], c["submercado"] or "?")] += (cap - antes) / 1000.0
        ultimo[ev["entidade_id"]] = (ev["data"], cap)
    resumo = {
        "eventos_importados": len(prontos),
        "usinas_casadas": len(r["casadas"]),
        "gw_casadas": round(sum(c["mw_ate_horizonte"] for c in r["casadas"]) / 1000, 2),
        "usinas_pendentes": len(r["pendencias"]),
        "gw_pendentes": round(sum(c["mw_ate_horizonte"] for c in r["pendencias"]) / 1000, 2),
        "usinas_no_deck": len(r["no_deck"]),
        "gw_no_deck": round(sum(c["mw_ate_horizonte"] for c in r["no_deck"]) / 1000, 2),
        "gw_por_ano_fonte_submercado": {f"{k[0]} {k[1]} {k[2]}": round(v, 3) for k, v in sorted(gw.items())},
    }
    (aux / "expansao_resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v for k, v in resumo.items() if k != "gw_por_ano_fonte_submercado"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
