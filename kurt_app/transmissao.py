"""Obras de transmissão previstas (ANEEL SIGET) como eventos de cenário.

Fonte: SIGET (Sistema de Gestão da Transmissão), conjuntos "Contrato" e
"Resolução" Empreendimento Obra Módulo, mais os módulos de linha (subestação de
origem e destino, extensão, circuito). Só entram obras "Em andamento" ou
"Planejado" do tipo "Instalação":

- LT nova de 230 kV ou mais: ``entra`` de um ramo entre as barras casadas das
  duas subestações;
- seccionamento ("originado do seccionamento do circuito LT ..."): ``sai`` da
  LT antiga e ``entra`` das duas novas; a subestação do meio vira barra nova
  quando não está no deck;
- transformação nova (módulo TR/AT com lado de alta de 230 kV ou mais):
  ``entra`` de um trafo entre as barras das duas tensões na mesma subestação.

Data: ``DatOprComEpd`` (operação comercial do empreendimento), senão
``DatOprComObr``; datas já passadas vão para o mês seguinte ao caso base.
Reatância: no seccionamento, a da LT antiga dividida pela extensão de cada
trecho; no trafo, a de um trafo paralelo da mesma subestação; na LT nova, nula
(marcado na nota). Substituição de equipamento, reator e banco de capacitor
ficam de fora.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from .expansao import (
    Casamento,
    casar_nome,
    escolher_barra,
    indice_subestacoes,
    proximo_mes,
    tokens,
)

SITUACOES = {"Em andamento", "Planejado"}
KV_MIN = 230.0
NOVA_MAX = 0.55  # abaixo disso a ponta não está no deck: subestação nova
RE_KV = re.compile(r"(\d+(?:[.,]\d+)?) kV")
RE_SEC = re.compile(
    r"seccionamento d[oa]s? (?:circuito |circuitos )?(?:da )?LT (\d+) kV (.+?) ?/ ?(.+?) C-?(\d+)(?: [A-Z]{2}(?:/[A-Z]{2})?)?(?: na SE (.+?))?(?:[,.;]|$| e )",
    re.IGNORECASE,
)
RE_TR = re.compile(r"^(?:TR|AT)\S* (\d+)/(\d+)(?:/[\d,.]+)? kV (.+?) (?:TRR?|ATR?|AT)\d*\S* ([A-Z]{2})$")
RE_MVA = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*MVA")


def _num(v: str | None) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    v = v.replace(".", "").replace(",", ".") if "," in v else v
    try:
        return float(v)
    except ValueError:
        return None


def _data_br(v: str | None) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", (v or "").strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _mes(d: date) -> date:
    return date(d.year, d.month, 1)


def montar(
    obras: list[dict],
    modulos_lt: list[dict],
    base: dict[str, list[dict]],
    data_base: date,
    rotulo_fonte: str,
    horizonte: date = date(2031, 12, 31),
) -> dict[str, Any]:
    barras = base["barras"]
    ramos = base["ramos"]
    idx = indice_subestacoes(barras)
    bar = {b["barra_id"]: b for b in barras}
    sub_de = {b["barra_id"]: (b.get("uf"), b["subestacao"]) for b in barras}
    ramos_entre: dict[frozenset, list[dict]] = defaultdict(list)
    for r in ramos:
        a, b = sub_de.get(r["de"]), sub_de.get(r["para"])
        if a and b:
            ramos_entre[frozenset((a, b))].append(r)
    lt_por_mdl = {m["IdeMdl"]: m for m in modulos_lt}
    primeiro = proximo_mes(data_base)

    vistos: set[str] = set()
    sel = []
    for o in obras:
        if o.get("DscSitObr") not in SITUACOES or o.get("DscTipObr") != "Instalação":
            continue
        if o["IdeMdl"] in vistos:
            continue
        vistos.add(o["IdeMdl"])
        sel.append(o)

    def data_obra(o: dict) -> date | None:
        d = _data_br(o.get("DatOprComEpd")) or _data_br(o.get("DatOprComObr"))
        if d is None:
            return None
        return _mes(max(d, primeiro))

    def casar(nome: str, uf: str, kv: float) -> Casamento:
        return casar_nome(nome, uf, kv, idx)

    eventos: list[dict] = []
    pendencias: list[dict] = []
    no_deck: list[dict] = []
    novas_barras: dict[tuple, str] = {}
    barras_criadas: dict[str, dict] = {}

    def pend(o: dict, motivo: str, cands: list | None = None) -> None:
        pendencias.append(
            {
                "IdeMdl": o["IdeMdl"],
                "modulo": o.get("NomMdl"),
                "obra": (o.get("DscObr") or "")[:300],
                "data": (data_obra(o) or "").__str__(),
                "motivo": motivo,
                "candidatos": cands or [],
            }
        )

    def ev(o: dict, d: date, acao: str, entidade: str, **kw: Any) -> dict:
        e = {
            "evento_id": f"siget-{o['IdeMdl']}-{acao}-{entidade}-{len(eventos)}",
            "data": d.isoformat(),
            "acao": acao,
            "entidade": entidade,
            "fonte": rotulo_fonte,
            "confianca": "contratado",
            "nota": f"SIGET módulo {o['IdeMdl']} ({o.get('IdeOnsMdl')}): {(o.get('DscObr') or '')[:240]}",
        }
        e.update(kw)
        eventos.append(e)
        return e

    def barra_nova(nome: str, uf: str, kv: float, vizinha: dict, o: dict, d: date) -> str:
        k = (uf, nome.upper(), kv)
        if k in novas_barras:
            return novas_barras[k]
        sub = re.sub(r"[^A-Z0-9]", "", "".join(tokens(nome)))[:6] or "NOVA"
        bid = f"BN-{sub}-{uf}{int(kv)}"
        novas_barras[k] = bid
        barras_criadas[bid] = {"area": vizinha.get("area"), "submercado": vizinha.get("submercado"), "kv": kv}
        ev(
            o,
            d,
            "entra",
            "barras",
            entidade_id=bid,
            registro={
                "barra_id": bid,
                "num_pwf": None,
                "nome": f"{nome} {int(kv)} kV (nova)",
                "kv": kv,
                "area": vizinha.get("area"),
                "submercado": vizinha.get("submercado"),
                "subestacao": f"{sub} (nova)",
                "uf": uf,
                "pos_x": None,
                "pos_y": None,
                "elevatoria": False,
            },
        )
        return bid

    def _ref(bid: str) -> dict:
        return bar.get(bid) or barras_criadas.get(bid, {})

    def ponta(nome: str, uf: str, kv: float) -> tuple[str | None, Casamento]:
        c = casar(nome, uf, kv)
        return c.barra_id, c

    def ramo_entre(a: str, b: str, circ: int | None) -> dict | None:
        if a not in bar or b not in bar:
            return None
        k = frozenset((sub_de[a], sub_de[b]))
        kv = min(bar[a]["kv"], bar[b]["kv"])
        rs = [
            r
            for r in ramos_entre.get(k, [])
            if r["tipo"] in ("linha", "csc") and min(bar[r["de"]]["kv"], bar[r["para"]]["kv"]) == kv
        ]
        if circ is not None:
            mesmo = [r for r in rs if r.get("circuito") == circ]
            if mesmo:
                return mesmo[0]
        return rs[0] if rs else None

    # ---- linhas de transmissão
    lts = [o for o in sel if o.get("SigTipMdl") == "LT"]
    grupos: dict[tuple, list[tuple[dict, dict]]] = defaultdict(list)
    n_lt = 0
    restantes: list[dict] = []
    lts.sort(key=lambda o: (data_obra(o) or date.max, o["IdeMdl"]))
    for passada in range(2):
        for o in lts if passada == 0 else restantes:
            m = lt_por_mdl.get(o["IdeMdl"])
            kv = _num(m.get("NumTensaoBaseLinhaTransm")) if m else None
            if kv is None:
                km = RE_KV.search(o.get("NomMdl") or "")
                kv = _num(km.group(1)) if km else None
            if kv is None or kv < KV_MIN:
                continue
            d = data_obra(o)
            if m is None or d is None:
                pend(o, "sem módulo de linha ou sem data")
                continue
            if d > horizonte:
                continue
            s = RE_SEC.search(o.get("DscObr") or "")
            if s:
                chave = (
                    s.group(2).strip().upper(),
                    s.group(3).strip().upper(),
                    int(s.group(4)),
                    kv,
                    m.get("SigUFSubestacaoOrigem"),
                )
                grupos[chave].append((o, m))
                continue
            a, ca = ponta(m["NomSubestacaoOrigem"], m["SigUFSubestacaoOrigem"], kv)
            b, cb = ponta(m["NomSubestacaoDestino"], m["SigUFSubestacaoDestino"], kv)
            # subestação nova: ponta sem nada parecido no deck (score < NOVA_MAX), com a outra casada ou já criada
            a = a or novas_barras.get((m["SigUFSubestacaoOrigem"], m["NomSubestacaoOrigem"].upper(), kv))
            b = b or novas_barras.get((m["SigUFSubestacaoDestino"], m["NomSubestacaoDestino"].upper(), kv))
            if passada == 0 and not a and not b:
                restantes.append(o)
                continue
            if not a and b and ca.score < NOVA_MAX:
                a = barra_nova(m["NomSubestacaoOrigem"], m["SigUFSubestacaoOrigem"], kv, _ref(b), o, d)
            elif not b and a and cb.score < NOVA_MAX:
                b = barra_nova(m["NomSubestacaoDestino"], m["SigUFSubestacaoDestino"], kv, _ref(a), o, d)
            if not a or not b or a == b:
                pend(
                    o,
                    f"ponta sem casamento ({m['NomSubestacaoOrigem']}: {ca.motivo or 'ok'}; {m['NomSubestacaoDestino']}: {cb.motivo or 'ok'})",
                    ca.candidatos + cb.candidatos,
                )
                continue
            circ = int(_num(m.get("NumCcuLinTms")) or 1)
            existente = ramo_entre(a, b, None)
            n_exist = (
                len([r for r in ramos_entre.get(frozenset((sub_de[a], sub_de[b])), []) if r["tipo"] == "linha"])
                if existente
                else 0
            )
            if existente is not None and n_exist >= circ:
                no_deck.append(
                    {
                        "IdeMdl": o["IdeMdl"],
                        "modulo": o.get("NomMdl"),
                        "ramo_deck": existente["ramo_id"],
                        "motivo": f"o deck já tem {n_exist} circuito(s) entre as subestações; a obra é o C{circ}",
                    }
                )
                continue
            n_lt += 1
            mva = RE_MVA.search(o.get("DscObr") or "")
            de, para = sorted((a, b))
            ev(
                o,
                d,
                "entra",
                "ramos",
                entidade_id=f"R{de[1:]}-{para[1:]}-S{o['IdeMdl']}",
                registro={
                    "ramo_id": f"R{de[1:]}-{para[1:]}-S{o['IdeMdl']}",
                    "de": de,
                    "para": para,
                    "circuito": circ,
                    "tipo": "linha",
                    "nome": m.get("NomLinTms") or o.get("NomMdl"),
                    "x_pct": existente["x_pct"] if existente else None,
                    "cap_normal_mva": _num(mva.group(1)) if mva else None,
                    "cap_emerg_mva": _num(mva.group(2)) if mva else None,
                    "estado_base": "ligado",
                },
            )
            if existente is None:
                eventos[-1]["nota"] += " | x_pct desconhecido (LT nova sem paralela no deck)" + (
                    " | subestação nova criada pelo SIGET" if a in barras_criadas or b in barras_criadas else ""
                )
            else:
                eventos[-1]["nota"] += f" | x_pct copiado do circuito paralelo {existente['ramo_id']}"

    # ---- seccionamentos
    n_sec = 0
    for (na, nb, circ, kv, uf), itens in grupos.items():
        o0 = itens[0][0]
        d = max(data_obra(o) for o, _ in itens)
        a, ca = ponta(na, uf, kv)
        b, cb = ponta(nb, uf, kv)
        antiga = ramo_entre(a, b, circ) if a and b and a != b else None
        if antiga is None:
            for o, _ in itens:
                pend(
                    o,
                    f"seccionamento: LT antiga {na} / {nb} C{circ} não encontrada no deck",
                    ca.candidatos + cb.candidatos,
                )
            continue
        if len(itens) != 2:
            for o, _ in itens:
                pend(o, f"seccionamento com {len(itens)} trecho(s) no SIGET (esperados 2)")
            continue
        # subestação do meio: a ponta de cada trecho que não é A nem B
        sa, sb = sub_de[a], sub_de[b]
        trechos = []
        meio_nome = None
        for o, m in itens:
            pts = [
                (m["NomSubestacaoOrigem"], m["SigUFSubestacaoOrigem"]),
                (m["NomSubestacaoDestino"], m["SigUFSubestacaoDestino"]),
            ]
            cas = [(p, ponta(p[0], p[1], kv)[0]) for p in pts]
            fixa = [x for x in cas if x[1] and sub_de[x[1]] in (sa, sb)]
            outra = [x for x in cas if not (x[1] and sub_de[x[1]] in (sa, sb))]
            if len(fixa) != 1 or len(outra) != 1:
                trechos = None
                break
            trechos.append((o, m, fixa[0][1], outra[0]))
            meio_nome = outra[0][0]
        if not trechos:
            for o, _ in itens:
                pend(o, "seccionamento: não deu para separar as pontas antigas da subestação nova")
            continue
        n_sec += 1
        (mn, muf) = meio_nome
        meio = ponta(mn, muf, kv)[0] or barra_nova(mn, muf, kv, bar[a], o0, d)
        ev(o0, d, "sai", "ramos", entidade_id=antiga["ramo_id"])
        kms = [_num(m.get("NumEtnLinTms")) for _, m, _, _ in trechos]
        tot = sum(k for k in kms if k) if all(kms) else None
        for (o, m, fixa, _), km in zip(trechos, kms, strict=True):
            frac = (km / tot) if tot else 0.5
            x = antiga["x_pct"] * frac if antiga.get("x_pct") is not None else None
            de, para = sorted((fixa, meio))
            rid = f"R{de.lstrip('B')}-{para.lstrip('B')}-S{o['IdeMdl']}"
            ev(
                o,
                d,
                "entra",
                "ramos",
                entidade_id=rid,
                registro={
                    "ramo_id": rid,
                    "de": de,
                    "para": para,
                    "circuito": int(_num(m.get("NumCcuLinTms")) or 1),
                    "tipo": "linha",
                    "nome": m.get("NomLinTms") or o.get("NomMdl"),
                    "x_pct": round(x, 4) if x is not None else None,
                    "cap_normal_mva": antiga.get("cap_normal_mva"),
                    "cap_emerg_mva": antiga.get("cap_emerg_mva"),
                    "estado_base": "ligado",
                },
            )
            eventos[-1]["nota"] += f" | seccionamento de {antiga['ramo_id']}; x_pct = {frac:.2f} do x da LT antiga" + (
                " (pela extensão)" if tot else " (metade, sem extensão)"
            )

    # ---- transformações
    n_tr = 0
    trs: dict[tuple, dict] = {}
    for o in sel:
        if o.get("SigTipMdl") != "ME":
            continue
        t = RE_TR.match((o.get("NomMdl") or "").strip())
        if not t:
            continue
        alta, baixa = float(t.group(1)), float(t.group(2))
        if alta < KV_MIN:
            continue
        trs.setdefault((t.group(3), t.group(4), alta, baixa, o["NomMdl"].split()[-2]), o)
    for (nome, uf, alta, baixa, rot), o in trs.items():
        d = data_obra(o)
        if d is None:
            pend(o, "transformação sem data")
            continue
        if d > horizonte:
            continue
        ha, ca = ponta(nome, uf, alta)
        if not ha:
            pend(o, f"transformação: subestação {nome} {alta:g} kV sem casamento ({ca.motivo})", ca.candidatos)
            continue
        sub = idx[uf][sub_de[ha][1]]
        hb = escolher_barra(sub, baixa)
        if hb is None:
            pend(o, f"transformação: {sub.nome} não tem barra de {baixa:g} kV no deck")
            continue
        hb = hb["barra_id"]
        paralelos = [
            r
            for r in ramos_entre.get(frozenset((sub_de[ha],)), [])
            if r["tipo"] == "trafo" and {bar[r["de"]]["kv"], bar[r["para"]]["kv"]} == {alta, baixa}
        ]
        n_idx = int(re.sub(r"\D", "", rot) or 0)
        if paralelos and len(paralelos) >= n_idx > 0:
            no_deck.append(
                {
                    "IdeMdl": o["IdeMdl"],
                    "modulo": o.get("NomMdl"),
                    "ramo_deck": paralelos[0]["ramo_id"],
                    "motivo": f"o deck já tem {len(paralelos)} trafo(s) {alta:g}/{baixa:g} kV na subestação; a obra é o {rot}",
                }
            )
            continue
        n_tr += 1
        de, para = sorted((ha, hb))
        rid = f"R{de.lstrip('B')}-{para.lstrip('B')}-S{o['IdeMdl']}"
        x = paralelos[0]["x_pct"] if paralelos else None
        ev(
            o,
            d,
            "entra",
            "ramos",
            entidade_id=rid,
            registro={
                "ramo_id": rid,
                "de": de,
                "para": para,
                "circuito": n_idx or None,
                "tipo": "trafo",
                "nome": o.get("NomMdl"),
                "x_pct": x,
                "cap_normal_mva": paralelos[0].get("cap_normal_mva") if paralelos else None,
                "cap_emerg_mva": paralelos[0].get("cap_emerg_mva") if paralelos else None,
                "estado_base": "ligado",
            },
        )
        eventos[-1]["nota"] += (
            f" | x_pct e capacidade do trafo paralelo {paralelos[0]['ramo_id']}"
            if paralelos
            else " | x_pct desconhecido (sem trafo paralelo no deck)"
        )

    # barra nova entra na data do primeiro ramo que a usa
    primeira: dict[str, str] = {}
    for e in eventos:
        if e["entidade"] == "ramos" and e["acao"] == "entra":
            for c in ("de", "para"):
                bid = e["registro"][c]
                if bid in barras_criadas:
                    primeira[bid] = min(primeira.get(bid, e["data"]), e["data"])
    for e in eventos:
        if e["entidade"] == "barras" and e["acao"] == "entra" and e["entidade_id"] in primeira:
            e["data"] = primeira[e["entidade_id"]]

    return {
        "eventos": eventos,
        "pendencias": pendencias,
        "no_deck": no_deck,
        "contagem": {
            "lt_novas": n_lt,
            "barras_em_subestacao_nova": len(barras_criadas),
            "seccionamentos": n_sec,
            "transformacoes": n_tr,
            "barras_novas": len(novas_barras),
        },
    }
