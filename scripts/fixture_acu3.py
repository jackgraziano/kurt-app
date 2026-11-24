"""Gera a fixture de teste a partir do recorte de Açu III do deck de 10/09/2026.

Entrada: o JSON extraído para o protótipo "Gargalo de Açu III"
(acu3_20260910_dados.json), com barras, ramos, restrições e as usinas dos
dados abertos de constrained-off.

Saída: fixtures/acu3_20260910/, no formato do cadastro.

O que é real e o que não é:
- barras, ramos e LPP vêm do deck oficial do DESSEM de 10/09/2026;
- cap_mw das usinas é aproximada pelo máximo da geração de referência do dia;
- carga_media_mw é a média das 48 meias-horas (Pl × DANC), não a carga do caso base;
- x_pct não está no recorte e fica nulo;
- os eventos marcados com fonte "demo" são hipotéticos e servem só para testar a
  linha do tempo. Nomes começam com [DEMO].

Uso: python scripts/fixture_acu3.py caminho/acu3_20260910_dados.json [saida]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from kurt_app.escrita import escrever, tabela  # noqa: E402
from kurt_app.layout import aplicar_posicoes  # noqa: E402

DATA = "2026-09-10"
BASE = {"vigente_de": DATA, "vigente_ate": None, "fonte": "deck", "confianca": "realizado"}
RE_NOME = re.compile(r"^(.{6})-?([A-Z]{2})(\d+)$")


def subestacao_uf(nome: str) -> tuple[str, str | None]:
    m = RE_NOME.match(nome)
    if not m:
        return nome, None
    return m.group(1).rstrip("-"), m.group(2)


def main() -> None:
    entrada = Path(sys.argv[1])
    saida = Path(sys.argv[2]) if len(sys.argv) > 2 else RAIZ / "fixtures" / "acu3_20260910"
    d = json.loads(entrada.read_text())

    barras = []
    for b in d["barras"]:
        sub, uf = subestacao_uf(b["nome"])
        carga = b.get("carga")
        barras.append(
            {
                "barra_id": f"B{b['num']}",
                "num_pwf": b["num"],
                "nome": b["nome"],
                "kv": float(b["kv"]),
                "submercado": "NE",
                "subestacao": sub,
                "uf": uf,
                "carga_media_mw": round(sum(carga) / len(carga), 1) if carga else None,
                "elevatoria": False,
                **BASE,
            }
        )
    nums = {b["num"] for b in d["barras"]}

    ramos: dict[tuple[int, int, int], dict] = {}

    def add_ramo(de: int, para: int, circ: int, tipo: str, nome: str | None, cap: float | None) -> None:
        a, b = sorted((de, para))
        k = (a, b, circ)
        if k in ramos:
            r = ramos[k]
            r["nome"] = r["nome"] or nome
            r["cap_normal_mva"] = r["cap_normal_mva"] or cap
            return
        ramos[k] = {
            "ramo_id": f"R{a}-{b}-{circ}",
            "de": f"B{de}",
            "para": f"B{para}",
            "circuito": circ,
            "tipo": tipo,
            "nome": nome,
            "x_pct": None,
            "cap_normal_mva": cap,
            "estado_base": "ligado",
            **BASE,
        }

    for de, para, rotulo, cap in d["topo"]:
        if de in nums and para in nums:
            tipo = "trafo" if rotulo.startswith("TR") else "linha"
            add_ramo(de, para, 1, tipo, None, float(cap))
    for f in d["fluxos"]:
        m = re.search(r"\(C(\d+)\)", f["nome"])
        circ = int(m.group(1)) if m else 1
        # o recorte de topologia não traz o número do circuito: se o par já existe, é o mesmo ramo
        a, b = sorted((f["de"], f["para"]))
        existente = next((k for k in ramos if k[0] == a and k[1] == b), None)
        if existente:
            ramos[existente]["nome"] = f["nome"]
            continue
        add_ramo(f["de"], f["para"], circ, "linha", f["nome"], float(f["lim_n"]))

    usinas = []
    for num, lista in sorted(d["ons"].items(), key=lambda kv: int(kv[0])):
        for i, u in enumerate(lista, start=1):
            usinas.append(
                {
                    "usina_id": f"U{num}-{i}",
                    "nome": u["n"],
                    "fonte_energia": {"E": "EOL", "S": "UFV"}.get(u["f"], u["f"]),
                    "barra_id": f"B{num}",
                    "submercado": "NE",
                    "cap_mw": float(max(u["ref"])),
                    "flag_corte": 1,
                    "ponto_conexao_ons": u.get("ponto"),
                    "grupo_eixo": "Açu III",
                    **BASE,
                }
            )

    restricoes = []
    for r in d["restr"]:
        restricoes.append(
            {
                "restr_id": f"LPP{r['lpp']}",
                "tipo": "LPP",
                "num_deck": r["lpp"],
                "nome": r["nome"],
                "formula_texto": r["formula"],
                "limites": [{"periodo": i + 1, "superior": v} for i, v in enumerate(r["limite"])],
                "io_ons": "IO-ON.NE.5NE",
                **BASE,
            }
        )

    # --- eventos de demonstração (hipotéticos) ---
    demo = {"fonte": "demo", "confianca": "especulativo"}
    ramos[(5350, 5924, 99)] = {
        "ramo_id": "R5350-5924-D1",
        "de": "B5350",
        "para": "B5924",
        "circuito": 2,
        "tipo": "linha",
        "nome": "[DEMO] LT 500 kV Açu III – Jaguaruana II C2",
        "cap_normal_mva": 2602.0,
        "estado_base": "ligado",
        "vigente_de": "2027-03-01",
        "vigente_ate": None,
        **demo,
    }
    usinas.append(
        {
            "usina_id": "U5350-D1",
            "nome": "[DEMO] UFV hipotética Açu III",
            "fonte_energia": "UFV",
            "barra_id": "B5350",
            "submercado": "NE",
            "cap_mw": 500.0,
            "flag_corte": 1,
            "grupo_eixo": "Açu III",
            "vigente_de": "2027-06-01",
            "vigente_ate": None,
            **demo,
        }
    )
    eventos = [
        {
            "evento_id": "E-DEMO-1",
            "data": "2027-03-01",
            "acao": "entra",
            "entidade": "ramos",
            "entidade_id": "R5350-5924-D1",
            "nota": "[DEMO] segundo circuito hipotético, só para testar a linha do tempo",
            **demo,
        },
        {
            "evento_id": "E-DEMO-2",
            "data": "2027-06-01",
            "acao": "entra",
            "entidade": "usinas",
            "entidade_id": "U5350-D1",
            "nota": "[DEMO] usina hipotética, só para testar a linha do tempo",
            **demo,
        },
    ]

    t_barras = tabela("barras", barras)
    t_ramos = tabela("ramos", list(ramos.values()))
    t_barras = aplicar_posicoes(t_barras, t_ramos)
    info = {
        "nome": "Recorte de Açu III, deck DESSEM de 10/09/2026",
        "tipo": "fixture",
        "origem": entrada.name,
        "avisos": [
            "cap_mw das usinas aproximada pelo máximo da geração de referência do dia",
            "carga_media_mw é a média das 48 meias-horas, não a carga do caso base",
            "x_pct ausente no recorte",
            "eventos com fonte 'demo' são hipotéticos",
        ],
    }
    escrever(
        saida,
        {
            "barras": t_barras,
            "ramos": t_ramos,
            "usinas": tabela("usinas", usinas),
            "restricoes": tabela("restricoes", restricoes),
            "eventos": tabela("eventos", eventos),
        },
        info,
    )
    print(f"fixture escrita em {saida}: {len(barras)} barras, {len(ramos)} ramos, {len(usinas)} usinas")


if __name__ == "__main__":
    main()
