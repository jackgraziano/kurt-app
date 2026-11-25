import { api } from "./api";
import type { DadosAba, DocInsumo, Linha, Resultados, RodadaResumo, SemanaSeries, Simulacao } from "./tipos";
import { aviso, esc, fmtData, rotuloMes } from "./util";

// Abas cujo nível vem dos decks DECOMP: Carga, Renováveis, Térmicas, Hidro, Valor da
// água e Execução. Cada uma mostra as opções (formulário), os avisos, um resumo das
// tabelas do DECOMP (editáveis pelo JSON) e, em Carga e Renováveis, o gráfico de uma
// semana com a forma semi-horária.

type Campo =
  | { nome: string; rotulo: string; tipo: "escolha"; opcoes: [string, string][] }
  | { nome: string; rotulo: string; tipo: "inteiro"; min: number; max: number }
  | { nome: string; rotulo: string; tipo: "texto" }
  | { nome: string; rotulo: string; tipo: "sim" };

const CAMPOS: Record<string, Campo[]> = {
  carga: [],
  renovaveis: [
    {
      nome: "nivel",
      rotulo: "Nível das usinas",
      tipo: "escolha",
      opcoes: [
        ["decomp", "somar os equivalentes do DECOMP"],
        ["capacidade", "capacidade × fator de capacidade histórico"],
      ],
    },
    {
      nome: "capacidade",
      rotulo: "Capacidade das usinas",
      tipo: "escolha",
      opcoes: [
        ["ons", "instalada do ONS (ou estimada de vários dias)"],
        ["caso", "cap_mw do caso de rede"],
      ],
    },
  ],
  termicas: [
    {
      nome: "estado_inicial",
      rotulo: "Estado inicial das unidades",
      tipo: "escolha",
      opcoes: [
        ["heuristica", "ligadas se houver inflexibilidade"],
        ["encadear", "fim do dia anterior (dias seguidos)"],
      ],
    },
  ],
  hidro: [
    {
      nome: "vazoes",
      rotulo: "Vazões",
      tipo: "escolha",
      opcoes: [
        ["deterministica", "previsão do deck (vazoes.rvN)"],
        ["cenarios_gevazp", "cenários do GEVAZP do mês seguinte"],
      ],
    },
  ],
  valor_agua: [
    {
      nome: "origem",
      rotulo: "Origem da FCF",
      tipo: "escolha",
      opcoes: [
        ["decks", "a de cada deck da aba Decks"],
        ["arquivo", "um par de arquivos para todo o horizonte"],
      ],
    },
    { nome: "cortdeco", rotulo: "cortdeco (caminho)", tipo: "texto" },
    { nome: "mapcut", rotulo: "mapcut (caminho)", tipo: "texto" },
  ],
  execucao: [
    {
      nome: "dias",
      rotulo: "Dias",
      tipo: "escolha",
      opcoes: [
        ["representativos", "representativos: um de cada tipo por mês"],
        ["todos", "todos os dias do horizonte"],
      ],
    },
    { nome: "cenarios", rotulo: "Cenários sorteados por dia", tipo: "inteiro", min: 1, max: 200 },
    { nome: "semente", rotulo: "Semente do sorteio", tipo: "inteiro", min: 0, max: 2147483647 },
    {
      nome: "discretizacao",
      rotulo: "Discretização",
      tipo: "escolha",
      opcoes: [
        ["semi-horaria", "semi-horária (48 intervalos)"],
        ["horaria", "horária (24 intervalos)"],
      ],
    },
    { nome: "paralelo", rotulo: "Rodadas em paralelo", tipo: "inteiro", min: 1, max: 64 },
    { nome: "rede", rotulo: "com a rede elétrica", tipo: "sim" },
  ],
};

const MINUTOS_POR_RODADA = 5; // um dia do SIN leva de 4 a 7 minutos

function campoHtml(c: Campo, v: unknown): string {
  if (c.tipo === "escolha")
    return `<label>${esc(c.rotulo)} <select name="${c.nome}">${c.opcoes
      .map(([k, r]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(r)}</option>`)
      .join("")}</select></label>`;
  if (c.tipo === "inteiro")
    return `<label>${esc(c.rotulo)} <input name="${c.nome}" type="number" min="${c.min}" max="${c.max}" value="${esc(v ?? "")}" /></label>`;
  if (c.tipo === "sim")
    return `<label class="check"><input type="checkbox" name="${c.nome}" ${v ? "checked" : ""} /> ${esc(c.rotulo)}</label>`;
  return `<label>${esc(c.rotulo)} <input name="${c.nome}" value="${esc(v ?? "")}" /></label>`;
}

function lerCampos(f: HTMLFormElement, campos: Campo[], antes: Record<string, unknown>): Record<string, unknown> {
  const fd = new FormData(f);
  const out: Record<string, unknown> = { ...antes };
  for (const c of campos) {
    if (c.tipo === "sim") out[c.nome] = fd.get(c.nome) === "on";
    else if (c.tipo === "inteiro") out[c.nome] = Number(fd.get(c.nome));
    else out[c.nome] = String(fd.get(c.nome) ?? "");
  }
  return out;
}

const fmt = (v: number) => v.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
const PATS = ["pesado", "médio", "leve"];

/** Tabela semana × chave com a média simples dos patamares (ou de um campo). */
function pivo(linhas: Linha[], chave: (r: Linha) => string, campo = "mwmed", soma = false): string {
  const semanas = [...new Set(linhas.map((r) => String(r.semana)))].sort();
  const chaves = [...new Set(linhas.map(chave))].sort();
  const acc = new Map<string, [number, number]>();
  for (const r of linhas) {
    const k = `${r.semana}|${chave(r)}|${soma ? r.patamar : ""}`;
    const a = acc.get(k) ?? [0, 0];
    a[0] += Number(r[campo] ?? 0);
    a[1] += 1;
    acc.set(k, a);
  }
  const valor = (s: string, c: string) => {
    if (!soma) {
      const a = acc.get(`${s}|${c}|`);
      return a ? a[0] / a[1] : null;
    }
    // soma das usinas, média dos patamares
    const v = [1, 2, 3].map((p) => acc.get(`${s}|${c}|${p}`)?.[0]).filter((x): x is number => x !== undefined);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  return `<div class="rolagem-x"><table class="pivo"><tr><th>semana</th>${chaves.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${semanas
    .map(
      (s) =>
        `<tr><th>${fmtData(s)}</th>${chaves
          .map((c) => {
            const v = valor(s, c);
            return `<td>${v === null ? "" : fmt(v)}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("")}</table></div>`;
}

function tabelasHtml(aba: string, d: DadosAba): string {
  const t = (n: string) => (d[n] as Linha[] | undefined) ?? [];
  const nota = `<p class="sub">Média simples dos três patamares, em MWmed. Os valores por patamar (${PATS.join(", ")}) estão no JSON da aba, onde podem ser mudados.</p>`;
  if (aba === "carga")
    return `<h3>Carga por submercado (DP)</h3>${nota}${pivo(t("carga"), (r) => String(r.submercado))}
      <h3>MMGD (PQ)</h3>${pivo(t("mmgd"), (r) => `${r.submercado} ${r.fonte}`)}
      <h3>ANDE (RI de Itaipu)</h3>${pivo(t("ande"), () => "ANDE")}`;
  if (aba === "renovaveis")
    return `<h3>Equivalentes do DECOMP (renovaveis.csv)</h3>${nota}${pivo(t("equivalentes"), (r) => `${r.fonte} ${r.submercado}`, "mwmed", true)}`;
  if (aba === "termicas") {
    const lin = t("termicas");
    return `<h3>Térmicas (CT): disponibilidade e inflexibilidade por submercado</h3>
      <p class="sub">Soma das usinas, em MW, média dos patamares. ${new Set(lin.map((r) => r.codigo)).size} usinas; CVU e valores por usina no JSON.</p>
      <h4>Disponibilidade</h4>${pivo(lin, (r) => String(r.submercado), "disp_mw", true)}
      <h4>Inflexibilidade</h4>${pivo(lin, (r) => String(r.submercado), "inflex_mw", true)}`;
  }
  if (aba === "hidro") {
    const vaz = t("vazoes");
    const vol = t("volumes");
    return `<h3>Vazões incrementais (vazoes.rvN)</h3>
      <p class="sub">${new Set(vaz.map((r) => r.posto)).size} postos, constantes dentro da semana. Soma das incrementais por semana, em m³/s:</p>
      ${pivo(vaz, () => "total", "m3s", true)}
      <h3>Volume inicial (UH, deck da primeira semana)</h3>
      <p class="sub">${vol.length} usinas; média ${vol.length ? (vol.reduce((a, r) => a + Number(r.vol_ini_pct ?? 0), 0) / vol.length).toFixed(1) : "–"}% do volume útil.</p>`;
  }
  if (aba === "valor_agua")
    return `<h3>FCF por semana</h3><table class="insumos">${(d.semanas ?? [])
      .map(
        (s) =>
          `<tr><th>${fmtData(String(s.semana))}</th><td>${esc(s.deck ?? "arquivo")}${s.estagio ? `, estágio ${s.estagio}` : ""}</td><td>${
            s.cortdeco ? `<span class="estado definido">com FCF</span>` : `<span class="estado a-definir">sem FCF</span>`
          }</td></tr>`,
      )
      .join("")}</table>`;
  if (aba === "execucao") {
    const r = d.resumo ?? { dias: 0, rodadas: 0 };
    const par = Number(d.opcoes.paralelo ?? 1);
    const horas = (r.rodadas * MINUTOS_POR_RODADA) / 60 / par;
    return `<h3>Plano</h3>
      <p><b>${r.dias}</b> dias × cenários = <b>${r.rodadas}</b> rodadas do Kurt, cerca de <b>${horas.toFixed(1)} h</b> com ${par} em paralelo (≈${MINUTOS_POR_RODADA} min por rodada).</p>
      <div class="rolagem-x"><table class="insumos">${(d.plano ?? [])
        .slice(0, 400)
        .map((p) => `<tr><th>${fmtData(p.data)}</th><td>${esc(p.tipo ?? "")}</td><td>peso ${p.peso}</td><td>${p.cenarios} cenário(s)</td></tr>`)
        .join("")}</table></div>`;
  }
  return "";
}

// ---------- gráfico de uma semana ----------
const CORES = ["#2f6fb2", "#d9822b", "#3a9a5b", "#a24ca8", "#c4372d", "#6b7280", "#1b998b", "#b5838d"];

function grafico(canvas: HTMLCanvasElement, sem: SemanaSeries, series: Record<string, number[]>): void {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const g = canvas.getContext("2d")!;
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  const m = { e: 64, d: 12, t: 12, b: 28 };
  const n = sem.patamar.length;
  const valores = Object.values(series).flat();
  const max = Math.max(1, ...valores) * 1.05;
  const x = (i: number) => m.e + ((w - m.e - m.d) * i) / (n - 1);
  const y = (v: number) => h - m.b - ((h - m.t - m.b) * v) / max;
  // patamares ao fundo
  const fundo = ["", "rgba(196,55,45,0.07)", "rgba(217,130,43,0.05)", "rgba(0,0,0,0)"];
  sem.patamar.forEach((p, i) => {
    g.fillStyle = fundo[p];
    g.fillRect(x(i) - (w - m.e - m.d) / (2 * n), m.t, (w - m.e - m.d) / n, h - m.t - m.b);
  });
  // eixos e grade
  g.strokeStyle = "#e3e6eb";
  g.fillStyle = "#667085";
  g.font = "11px system-ui, sans-serif";
  g.textAlign = "right";
  for (let k = 0; k <= 4; k++) {
    const v = (max * k) / 4;
    g.beginPath();
    g.moveTo(m.e, y(v));
    g.lineTo(w - m.d, y(v));
    g.stroke();
    g.fillText(fmt(v), m.e - 6, y(v) + 4);
  }
  g.textAlign = "center";
  sem.dias.forEach((d, i) => g.fillText(fmtData(d).slice(0, 5), x(i * 48 + 24), h - 8));
  Object.entries(series).forEach(([, v], k) => {
    g.strokeStyle = CORES[k % CORES.length];
    g.lineWidth = 1.6;
    g.beginPath();
    v.forEach((val, i) => (i ? g.lineTo(x(i), y(val)) : g.moveTo(x(i), y(val))));
    g.stroke();
  });
}

async function painelSemana(el: HTMLElement, s: Simulacao, aba: string, semanas: string[], cenarios: number): Promise<void> {
  el.innerHTML = `<h3>Forma semi-horária de uma semana</h3>
    <div class="controles-grafico">
      <label>Semana <select id="g-semana">${semanas.map((x) => `<option value="${x}">${fmtData(x)}</option>`).join("")}</select></label>
      <label>Cenário <select id="g-cenario">${Array.from({ length: cenarios + 1 }, (_, k) => `<option value="${k}">${k === 0 ? "determinístico" : `sorteio ${k}`}</option>`).join("")}</select></label>
      <span class="sub">fundo: patamar pesado (vermelho), médio (laranja), leve (branco)</span>
    </div>
    <div id="g-legenda" class="legenda-grafico"></div>
    <canvas id="g-canvas" class="grafico-semana"></canvas>
    <p class="sub" id="g-nota"></p>`;
  const desenhar = async () => {
    const inicio = (el.querySelector("#g-semana") as HTMLSelectElement).value;
    const cen = Number((el.querySelector("#g-cenario") as HTMLSelectElement).value);
    try {
      const sem = await api.semana(s.nome, inicio, cen);
      const series: Record<string, number[]> =
        aba === "carga"
          ? { ...Object.fromEntries(Object.entries(sem.carga).map(([k, v]) => [`carga ${k}`, v])), "MMGD SE": sem.mmgd.SE ?? [] }
          : Object.fromEntries(Object.entries(sem.renovaveis));
      el.querySelector("#g-legenda")!.innerHTML = Object.keys(series)
        .map((k, i) => `<span><i style="background:${CORES[i % CORES.length]}"></i>${esc(k)}</span>`)
        .join("");
      grafico(el.querySelector("#g-canvas") as HTMLCanvasElement, sem, series);
      el.querySelector("#g-nota")!.textContent =
        aba === "renovaveis"
          ? `Soma das usinas do caso de rede, em MW. Fator aplicado para bater com o DECOMP: ${Object.entries(sem.fator_renovaveis)
              .map(([k, v]) => `${k} ${v === null ? "–" : v.toFixed(2)}`)
              .join(" · ")}${
              Object.keys(sem.fator_fora_da_faixa).length
                ? `. Fora da faixa 0,5 a 2 (limitado; o equivalente do DECOMP talvez não cubra essas usinas): ${Object.entries(sem.fator_fora_da_faixa)
                    .map(([k, v]) => `${k} ${v.toFixed(2)}`)
                    .join(" · ")}`
                : ""
            }`
          : "MW por submercado. A média de cada patamar na semana bate com o DECOMP.";
    } catch (err) {
      el.querySelector("#g-nota")!.textContent = String(err);
    }
  };
  el.querySelector("#g-semana")!.addEventListener("change", desenhar);
  el.querySelector("#g-cenario")!.addEventListener("change", desenhar);
  await desenhar();
}

export async function abrirAbaDecomp(el: HTMLElement, s: Simulacao, aba: string, rotulo: string, descricao: string, aoMudar: () => Promise<void>): Promise<void> {
  el.innerHTML = `<div class="pagina larga"><h2>${esc(rotulo)}</h2><p class="sub">${esc(descricao)}</p><p class="sub">carregando…</p></div>`;
  let doc: DocInsumo<DadosAba>;
  try {
    doc = await api.insumo<DadosAba>(s.nome, aba);
  } catch (err) {
    el.innerHTML = `<div class="pagina larga"><h2>${esc(rotulo)}</h2><p class="sub">${esc(descricao)}</p>
      <p class="alerta">${esc(String(err).replace(/^Error: /, ""))}</p>
      <p><a href="#/c/${esc(s.nome)}/decks">ir para a aba Decks</a></p></div>`;
    return;
  }
  const d = doc.dados;
  const campos = CAMPOS[aba] ?? [];
  const sob = (s.insumos[aba] as { sobrescritos?: Record<string, unknown[]> } | undefined)?.sobrescritos ?? {};
  const nSob = Object.values(sob).reduce((a, v) => a + v.length, 0);
  const semanas = [...new Set(((d.carga ?? d.equivalentes ?? []) as Linha[]).map((r) => String(r.semana)))].sort();
  const exec = (s.insumos.execucao as { opcoes?: { cenarios?: number } } | undefined)?.opcoes;
  el.innerHTML = `<div class="pagina larga">
    <h2>${esc(rotulo)}</h2>
    <p class="sub">${esc(descricao)}</p>
    ${campos.length ? `<form id="f-aba" class="cartao">${campos.map((c) => campoHtml(c, d.opcoes[c.nome])).join("")}<div><button class="primario">salvar</button></div></form>` : ""}
    ${(d.avisos ?? []).map((a) => `<p class="alerta">${esc(a)}</p>`).join("")}
    ${nSob ? `<p class="alerta">${nSob} valor(es) mudados à mão pelo JSON; o resto segue o DECOMP.</p>` : ""}
    <div id="painel-semana"></div>
    ${tabelasHtml(aba, d)}
  </div>`;
  const f = el.querySelector<HTMLFormElement>("#f-aba");
  f?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    // reenvia o documento inteiro (com as tabelas) para não perder sobrescritos
    const dados = { ...d, opcoes: lerCampos(f, campos, d.opcoes) };
    try {
      await api.subirInsumo(s.nome, aba, { formato: doc.formato, versao: doc.versao, insumo: aba, dados });
      aviso("aba salva");
      await aoMudar();
    } catch (err) {
      aviso(String(err), true);
    }
  });
  if (aba === "execucao") await painelRodadas(el.querySelector("#painel-semana")!, s);
  if ((aba === "carga" || aba === "renovaveis") && semanas.length)
    await painelSemana(el.querySelector("#painel-semana")!, s, aba, semanas, exec?.cenarios ?? 5);
}

// ---------- rodadas e resultados (aba Execução) ----------
let vigia = 0;

async function painelRodadas(el: HTMLElement, s: Simulacao): Promise<void> {
  window.clearTimeout(vigia);
  let lista: RodadaResumo[] = [];
  try {
    lista = await api.rodadas(s.nome);
  } catch (err) {
    el.innerHTML = `<p class="erro">${esc(String(err))}</p>`;
    return;
  }
  const ativa = lista.find((r) => r.status === "rodando");
  el.innerHTML = `<h3>Rodadas</h3>
    <p><button class="primario" id="disparar" ${ativa ? "disabled" : ""}>disparar rodada</button>
    ${ativa ? `<button class="perigo" id="cancelar">cancelar a rodada em andamento</button>` : ""}</p>
    ${
      lista.length
        ? `<table class="insumos">${lista
            .map((r) => {
              const c = r.contagem;
              const pct = Math.round((100 * (c.ok + c.erro + c.cancelada)) / Math.max(c.total, 1));
              return `<tr><th>${esc(r.id)}</th><td>${esc(r.status)} · ${c.ok} ok, ${c.erro} com erro, ${c.rodando} rodando, ${c["na fila"]} na fila (${pct}%)</td>
                <td><button data-res="${esc(r.id)}" ${c.ok ? "" : "disabled"}>ver resultados</button></td></tr>`;
            })
            .join("")}</table>`
        : `<p class="sub">Nenhuma rodada ainda. Cada rodada executa o plano abaixo: uma pasta do dia por dia e cenário, e o Kurt em cada uma.</p>`
    }
    <div id="resultados"></div>`;
  el.querySelector("#disparar")?.addEventListener("click", async () => {
    try {
      await api.dispararRodada(s.nome);
      aviso("rodada disparada");
      await painelRodadas(el, s);
    } catch (err) {
      aviso(String(err), true);
    }
  });
  el.querySelector("#cancelar")?.addEventListener("click", async () => {
    if (!ativa || !window.confirm("Cancelar a rodada em andamento? As tarefas prontas ficam.")) return;
    try {
      await api.cancelarRodada(s.nome, ativa.id);
      await painelRodadas(el, s);
    } catch (err) {
      aviso(String(err), true);
    }
  });
  el.querySelectorAll<HTMLButtonElement>("button[data-res]").forEach((b) =>
    b.addEventListener("click", () => void mostrarResultados(el.querySelector("#resultados")!, s, b.dataset.res!)),
  );
  if (ativa && document.body.contains(el)) vigia = window.setTimeout(() => void painelRodadas(el, s), 5000);
}

async function mostrarResultados(el: HTMLElement, s: Simulacao, id: string): Promise<void> {
  el.innerHTML = `<p class="sub">carregando…</p>`;
  let r: Resultados;
  try {
    r = await api.resultados(s.nome, id);
  } catch (err) {
    el.innerHTML = `<p class="erro">${esc(String(err))}</p>`;
    return;
  }
  const q = (x: { p10: number; p50: number; p90: number }) =>
    `<td>${x.p10.toFixed(1)}</td><td><b>${x.p50.toFixed(1)}</b></td><td>${x.p90.toFixed(1)}</td>`;
  const subs = ["SIN", "SE", "S", "NE", "N"];
  const linhas = r.meses
    .filter((m) => subs.includes(m.submercado))
    .sort((a, b) => a.mes.localeCompare(b.mes) || subs.indexOf(a.submercado) - subs.indexOf(b.submercado) || a.tipo.localeCompare(b.tipo))
    .map((m) => `<tr><th>${rotuloMes(m.mes)}</th><td>${esc(m.submercado)}</td><td>${esc(m.tipo)}</td>${q(m)}</tr>`)
    .join("");
  el.innerHTML = `<h3>Resultados da rodada ${esc(r.id)}</h3>
    <p class="sub">Corte de eólicas e solares em ${esc(r.unidade)} por mês, entre ${r.cenarios} cenário(s). ${r.contagem.ok} de ${r.contagem.total} tarefas ok.
    ENE = corte energético; rede = por restrição elétrica.</p>
    <div class="rolagem-x"><table class="pivo"><tr><th>mês</th><th>submercado</th><th>tipo</th><th>P10</th><th>P50</th><th>P90</th></tr>${linhas}</table></div>
    <h4>Usinas com mais corte (P50 do horizonte)</h4>
    <div class="rolagem-x"><table class="pivo"><tr><th>usina</th><th>submercado</th><th>fonte</th><th>P10</th><th>P50</th><th>P90</th></tr>${r.usinas
      .map((u) => `<tr><th>${esc(u.usina_id)}</th><td>${esc(u.submercado)}</td><td>${esc(u.fonte)}</td>${q(u)}</tr>`)
      .join("")}</table></div>`;
}
