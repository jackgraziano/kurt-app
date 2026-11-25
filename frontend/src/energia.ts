import { api } from "./api";
import type { Energia, RegraNivel, Sensibilidade, Simulacao } from "./tipos";
import { esc, fmtData, rotuloMes } from "./util";

// Energia em GWmed por mês, semana operativa ou dia, nas abas Carga e Renováveis.
// O servidor devolve o MW médio de cada dia antes das sensibilidades; aqui aplicamos as
// regras de nível (fator por dia) e o usuário edita o alvo em GWmed de qualquer célula.
// Editar uma célula grava uma regra {fonte, submercado, fator, meses | datas} no JSON.

const SUBS = ["SE", "S", "NE", "N"];
type Grade = "mes" | "semana" | "dia";

interface Periodo {
  rotulo: string;
  dias: string[];
  regra: { meses: string[] } | { datas: string[] };
}

function casa(r: RegraNivel, dia: string): boolean {
  if (r.meses?.length) return r.meses.includes(dia.slice(0, 7));
  if (r.datas?.length) return r.datas.includes(dia);
  return true;
}

function fator(nivel: RegraNivel[], fonte: string, sub: string, dia: string): number {
  return nivel
    .filter((r) => r.fonte === fonte && ((r.submercado ?? "todos") === "todos" || r.submercado === sub) && casa(r, dia))
    .reduce((a, r) => a * r.fator, 1);
}

function mesmoAlvo(r: RegraNivel, fonte: string, sub: string, p: Periodo): boolean {
  if (r.fonte !== fonte || (r.submercado ?? "todos") !== sub) return false;
  const a = "meses" in p.regra ? { meses: p.regra.meses } : { datas: p.regra.datas };
  return JSON.stringify(r.meses ?? []) === JSON.stringify(a.meses ?? []) && JSON.stringify(r.datas ?? []) === JSON.stringify(a.datas ?? []);
}

export async function painelEnergia(
  el: HTMLElement,
  s: Simulacao,
  fontes: [string, string][],
  semanas: string[],
  sens: Sensibilidade,
  salvar: (s: Sensibilidade) => Promise<void>,
): Promise<void> {
  let nivel: RegraNivel[] = structuredClone(sens.nivel ?? []);
  let sujo = false;
  let grade: Grade = "mes";
  let fonte = fontes[0][0];
  const base = new Map<string, Record<string, number>>(); // dia -> grupo -> MW médio
  el.innerHTML = `<div class="cartao largo">
    <h3>Energia por período (GWmed)</h3>
    <p class="sub">Média do período em GW médios, já com as sensibilidades. Para mudar, digite o valor desejado numa célula:
    o app grava um fator para aquele período e submercado (a coluna SIN vale para todos os submercados juntos).
    Abaixo de cada valor, em cinza, o que vem do DECOMP sem ajustes.</p>
    <div class="controles-grafico">
      <label>Período <select id="e-grade"><option value="mes">mês</option><option value="semana">semana operativa</option><option value="dia">dia</option></select></label>
      ${fontes.length > 1 ? `<label>Fonte <select id="e-fonte">${fontes.map(([f, r]) => `<option value="${f}">${esc(r)}</option>`).join("")}</select></label>` : ""}
      <span class="sub" id="e-estado"></span>
    </div>
    <div id="e-tabela" class="rolagem"></div>
    <div id="e-regras"></div>
    <div><button class="primario" id="e-salvar" disabled>salvar ajustes</button> <button type="button" id="e-desfazer" disabled>descartar</button></div>
  </div>`;
  const $ = <T extends HTMLElement>(q: string) => el.querySelector(q) as T;

  const periodos = (): Periodo[] => {
    const dias = [...base.keys()].filter((d) => d >= s.inicio && d <= s.fim).sort();
    if (grade === "dia") return dias.map((d) => ({ rotulo: fmtData(d), dias: [d], regra: { datas: [d] } }));
    if (grade === "mes") {
      const meses = [...new Set(dias.map((d) => d.slice(0, 7)))];
      return meses.map((m) => ({ rotulo: rotuloMes(m), dias: dias.filter((d) => d.startsWith(m)), regra: { meses: [m] } }));
    }
    return semanas
      .map((w) => {
        const todos = [...base.keys()].filter((d) => d >= w && d < proxima(w)).sort();
        return { rotulo: `sem. ${fmtData(w)}`, dias: todos.filter((d) => d >= s.inicio && d <= s.fim), regra: { datas: todos } };
      })
      .filter((p) => p.dias.length);
  };
  const valor = (p: Periodo, sub: string, ajustado: boolean): number => {
    const subs = sub === "todos" ? SUBS : [sub];
    let soma = 0;
    for (const d of p.dias)
      for (const x of subs) soma += (base.get(d)?.[`${fonte} ${x}`] ?? 0) * (ajustado ? fator(nivel, fonte, x, d) : 1);
    return soma / p.dias.length / 1000;
  };
  const fmt = (v: number) => v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const desenhar = () => {
    const ps = periodos();
    const cols = [...SUBS, "todos"];
    $("#e-tabela").innerHTML = ps.length
      ? `<table class="sens energia"><tr><th></th>${cols.map((c) => `<th>${c === "todos" ? "SIN" : c}</th>`).join("")}</tr>${ps
          .map(
            (p, i) =>
              `<tr><th>${esc(p.rotulo)}${p.dias.length < (grade === "semana" ? 7 : 1) ? ` <small class="sub">(${p.dias.length} d)</small>` : ""}</th>${cols
                .map((c) => {
                  const a = valor(p, c, true);
                  const b = valor(p, c, false);
                  const mudou = Math.abs(a - b) > Math.max(1e-4, Math.abs(b) * 1e-4);
                  return `<td class="${mudou ? "mudou" : ""}"><input type="number" step="0.01" min="0" data-p="${i}" data-c="${c}" value="${a.toFixed(2)}" />${
                    mudou ? `<small>${fmt(b)} (${a >= b ? "+" : ""}${(((a - b) / (b || 1)) * 100).toFixed(1)}%)</small>` : ""
                  }</td>`;
                })
                .join("")}</tr>`,
          )
          .join("")}</table>`
      : `<p class="sub">sem dados ainda</p>`;
    $("#e-tabela")
      .querySelectorAll<HTMLInputElement>("input")
      .forEach((inp) =>
        inp.addEventListener("change", () => {
          const p = ps[Number(inp.dataset.p)];
          const c = inp.dataset.c!;
          const alvo = Number(inp.value);
          const atual = valor(p, c, true);
          if (!(alvo >= 0) || atual <= 0) return void window.setTimeout(desenhar);
          const k = alvo / atual;
          const r = nivel.find((x) => mesmoAlvo(x, fonte, c, p));
          if (r) r.fator *= k;
          else nivel.push({ fonte, submercado: c, fator: k, ...p.regra });
          nivel = nivel.filter((x) => Math.abs(x.fator - 1) > 1e-9);
          sujo = true;
          window.setTimeout(desenhar); // fora do evento: a troca da tabela no blur quebra o DOM
        }),
      );
    regras();
    ($("#e-salvar") as HTMLButtonElement).disabled = !sujo;
    ($("#e-desfazer") as HTMLButtonElement).disabled = !sujo;
  };

  const regras = () => {
    const minhas = nivel.map((r, i) => [r, i] as const).filter(([r]) => fontes.some(([f]) => f === r.fonte));
    $("#e-regras").innerHTML = minhas.length
      ? `<h4>Ajustes de nível ${sujo ? "(não salvos)" : ""}</h4><ul class="regras">${minhas
          .map(
            ([r, i]) =>
              `<li>${esc(fontes.find(([f]) => f === r.fonte)?.[1] ?? r.fonte)} · ${esc(r.submercado === "todos" ? "SIN" : r.submercado)} · ${
                r.meses?.length ? r.meses.map(rotuloMes).join(", ") : r.datas?.length ? quando(r.datas) : "todo o horizonte"
              }: <b>${r.fator >= 1 ? "+" : ""}${((r.fator - 1) * 100).toFixed(1)}%</b> <button type="button" data-i="${i}">remover</button></li>`,
          )
          .join("")}</ul>`
      : "";
    $("#e-regras")
      .querySelectorAll<HTMLButtonElement>("button[data-i]")
      .forEach((b) =>
        b.addEventListener("click", () => {
          nivel.splice(Number(b.dataset.i), 1);
          sujo = true;
          desenhar();
        }),
      );
  };

  $("#e-grade").addEventListener("change", (ev) => {
    grade = (ev.target as HTMLSelectElement).value as Grade;
    desenhar();
  });
  el.querySelector("#e-fonte")?.addEventListener("change", (ev) => {
    fonte = (ev.target as HTMLSelectElement).value;
    desenhar();
  });
  $("#e-desfazer").addEventListener("click", () => {
    nivel = structuredClone(sens.nivel ?? []);
    sujo = false;
    desenhar();
  });
  $("#e-salvar").addEventListener("click", async () => {
    ($("#e-salvar") as HTMLButtonElement).disabled = true;
    await salvar({ nivel: nivel.map((r) => ({ ...r, fator: Math.round(r.fator * 1e6) / 1e6 })), forma: sens.forma ?? {} });
  });

  // carrega semana a semana (a primeira vez pode levar alguns segundos por semana no SIN)
  const falhas: string[] = [];
  for (const [i, w] of semanas.entries()) {
    $("#e-estado").textContent = `calculando semana ${i + 1} de ${semanas.length}…`;
    try {
      const e: Energia = await api.energia(s.nome, w);
      e.dias.forEach((d, j) => base.set(d, Object.fromEntries(Object.entries(e.base).map(([g, v]) => [g, v[j]]))));
    } catch (err) {
      falhas.push(`${fmtData(w)}: ${String(err).replace(/^Error: /, "")}`);
    }
    if (!el.isConnected) return;
    desenhar();
  }
  $("#e-estado").textContent = falhas.length ? `sem dados para a(s) semana(s) ${falhas.join("; ")}` : "";
}

function proxima(w: string): string {
  const d = new Date(`${w}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7);
  return d.toISOString().slice(0, 10);
}

function quando(datas: string[]): string {
  return datas.length === 1 ? fmtData(datas[0]) : `${fmtData(datas[0])} a ${fmtData(datas[datas.length - 1])}`;
}
