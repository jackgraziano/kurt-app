"""Obras de transmissão previstas (ANEEL SIGET) no cenário de expansão.

Uso (depois de scripts/expansao_ralie.py):
    .venv/bin/python scripts/expansao_transmissao.py --cadastro dados/cadastro/base-20261120 \
        [--cenario expansao] [--data 2026-11-24] [--sem-download]

Baixa os CSV do SIGET para dados/fontes/aneel/<data>/siget/, monta os eventos
(kurt_app.transmissao) e os acrescenta ao cenário. Grava em
dados/cenarios/<cadastro>/: transmissao_pendencias.csv, transmissao_no_deck.csv
e expansao_arquivos/transmissao_eventos.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from kurt_app.cadastro import Cadastro  # noqa: E402
from kurt_app.cenario import Cenarios  # noqa: E402
from kurt_app.transmissao import montar  # noqa: E402

PACOTE = "https://dadosabertos.aneel.gov.br/dataset/beefe870-7452-4830-a7b0-6611e3d5eff6/resource"
ARQUIVOS = {
    "siget-contrato-empreendimento-obra-modulo.csv": "a1366325-1071-4eeb-94bd-27915747b177",
    "siget-resolucao-empreendimento-obra-modulo.csv": "1c345514-893d-44a1-8e7e-a9362137c356",
    "siget-contrato-modulolinhatransmissao-subestacaoorigem-subestacaodestino.csv": "89caca18-e9a8-484c-8772-c67b456d801a",
    "siget-resolucao-modulolinhatransmissao-subestacaoorigem-subestacaodestino.csv": "e5c1141e-c08c-46a5-bb55-68d66152033a",
}


def ler(arq: Path) -> list[dict]:
    with arq.open(encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def gravar_csv(arq: Path, linhas: list[dict], colunas: list[str]) -> None:
    with arq.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas, extrasaction="ignore")
        w.writeheader()
        for r in linhas:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cadastro", required=True)
    p.add_argument("--cenario", default="expansao")
    p.add_argument("--data", default=date.today().isoformat())
    p.add_argument("--sem-download", action="store_true")
    p.add_argument("--horizonte", default="2031-12-31")
    a = p.parse_args()

    pasta = RAIZ / "dados" / "fontes" / "aneel" / a.data / "siget"
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, rid in ARQUIVOS.items():
        alvo = pasta / nome
        if not (alvo.exists() and alvo.stat().st_size):
            if a.sem_download:
                raise SystemExit(f"{alvo} não existe")
            req = urllib.request.Request(f"{PACOTE}/{rid}/download/{nome}", headers={"User-Agent": "kurt-app"})
            with urllib.request.urlopen(req, timeout=300) as r:
                alvo.write_bytes(r.read())
    obras = ler(pasta / "siget-contrato-empreendimento-obra-modulo.csv") + ler(pasta / "siget-resolucao-empreendimento-obra-modulo.csv")
    lts = ler(pasta / "siget-contrato-modulolinhatransmissao-subestacaoorigem-subestacaodestino.csv") + ler(
        pasta / "siget-resolucao-modulolinhatransmissao-subestacaoorigem-subestacaodestino.csv"
    )
    data_siget = max((o.get("DatGeracaoConjuntoDados") or "")[:10] for o in obras) or a.data

    pasta_cad = Path(a.cadastro)
    base = {t: pq.read_table(pasta_cad / f"{t}.parquet").to_pylist() for t in ("barras", "ramos")}
    cad = Cadastro(pasta_cad)
    r = montar(obras, lts, base, date.fromisoformat(cad.data_base()), f"ANEEL SIGET {data_siget}", date.fromisoformat(a.horizonte))

    saida = RAIZ / "dados" / "cenarios" / pasta_cad.resolve().name
    aux = saida / "expansao_arquivos"
    aux.mkdir(parents=True, exist_ok=True)
    (aux / "transmissao_eventos.json").write_text(json.dumps(r["eventos"], ensure_ascii=False, indent=1) + "\n")
    gravar_csv(saida / "transmissao_pendencias.csv", r["pendencias"], ["IdeMdl", "modulo", "data", "motivo", "candidatos", "obra"])
    gravar_csv(saida / "transmissao_no_deck.csv", r["no_deck"], ["IdeMdl", "modulo", "ramo_deck", "motivo"])

    cens = Cenarios(cad, saida)
    ja = {e["evento_id"] for e in cens.ler(a.cenario).get("eventos", [])}
    novos = [e for e in r["eventos"] if e["evento_id"] not in ja]
    prontos = cens.adicionar_eventos(a.cenario, novos)
    print(json.dumps({**r["contagem"], "eventos_importados": len(prontos), "pendencias": len(r["pendencias"]), "ja_no_deck": len(r["no_deck"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
