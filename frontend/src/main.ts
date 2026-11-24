import "./estilo.css";
import { api } from "./api";
import { COR_ESTADO, COR_FONTE, Mapa, type Alvo, type Cena, type Estado } from "./mapa";
import type { CenarioDetalhe, CenarioResumo, Configuracao, Diferenca, Evento, EventoCenario, Ficha, Meta } from "./tipos";

// ---------- utilidades ----------
const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const fmtData = (iso: string) => iso.split("-").reverse().join("/");
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const rotuloMes = (m: string) => `${MESES[Number(m.slice(5, 7)) - 1]}/${m.slice(2, 4)}`;
const fimDoMes = (m: string) => {
  const [a, mm] = m.split("-").map(Number);
  return new Date(Date.UTC(a, mm, 0)).toISOString().slice(0, 10);
};
const num = (v: unknown, casas = 0) =>
  typeof v === "number" ? v.toLocaleString("pt-BR", { maximumFractionDigits: casas }) : esc(v);

function listaMeses(inicio: string, fim: string): string[] {
  const out: string[] = [];
  let [a, m] = inicio.slice(0, 7).split("-").map(Number);
  const [af, mf] = fim.slice(0, 7).split("-").map(Number);
  while (a < af || (a === af && m <= mf)) {
    out.push(`${a}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      a += 1;
    }
  }
  return out;
}

function aviso(msg: string, erro = false): void {
  const el = $("#aviso");
  el.textContent = msg;
  el.className = erro ? "erro" : "";
  el.hidden = false;
  window.setTimeout(() => (el.hidden = true), erro ? 9000 : 3500);
}

// ---------- estrutura da página ----------
$("#app").innerHTML = `
<header>
  <div class="marca">kurt-app <span id="cadastro-nome"></span></div>
  <div class="controles">
    <label>Cenário <select id="cenario"></select></label>
    <label>Tensão
      <select id="kv">
        <option value="">todas</option>
        <option value="230" selected>≥ 230 kV</option>
        <option value="345">≥ 345 kV</option>
        <option value="500">≥ 500 kV</option>
      </select>
    </label>
    <label>Submercado
      <select id="sub"><option value="">todos</option><option>SE</option><option>S</option><option>NE</option><option>N</option></select>
    </label>
    <label title="Micro e minigeração distribuída"><input type="checkbox" id="mmgd" /> MMGD</label>
    <label class="comparar"><input type="checkbox" id="cmp-on" /> comparar com <select id="cmp-mes"></select></label>
    <button id="ajustar" title="Enquadrar o mapa">enquadrar</button>
  </div>
</header>
<section class="linha-tempo">
  <button id="play" title="Animar mês a mês">▶</button>
  <div class="trilho">
    <div id="marcas"></div>
    <div id="marca-base" title="Data do deck do caso base"></div>
    <input type="range" id="mes" min="0" max="0" value="0" />
  </div>
  <div id="mes-rotulo"></div>
</section>
<main>
  <div class="mapa-wrap">
    <canvas id="mapa"></canvas>
    <div id="dica" hidden></div>
    <div class="legenda" id="legenda"></div>
    <div class="resumo" id="resumo"></div>
    <div id="aviso" hidden></div>
  </div>
  <aside>
    <nav>
      <button data-aba="eventos" class="ativa">Eventos</button>
      <button data-aba="cenario">Cenário</button>
      <button data-aba="ficha">Ficha</button>
      <button data-aba="validacao">Validação</button>
      <button data-aba="sobre">Sobre</button>
    </nav>
    <div id="aba-eventos" class="aba"></div>
    <div id="aba-cenario" class="aba" hidden></div>
    <div id="aba-ficha" class="aba" hidden><p class="vazio">Clique numa subestação, linha ou usina.</p></div>
    <div id="aba-validacao" class="aba" hidden></div>
    <div id="aba-sobre" class="aba" hidden></div>
  </aside>
</main>
<datalist id="lista-barras"></datalist>`;

const mapa = new Mapa($<HTMLCanvasElement>("#mapa"));
api.ufs().then((u) => mapa.definirUfs(u)).catch(() => {}); // fundo opcional
let meta: Meta;
let meses: string[] = [];
let cenario = new URLSearchParams(location.search).get("cenario") ?? "base";
let cenarios: CenarioResumo[] = [];
let detalhe: CenarioDetalhe | null = null;
let pedido = 0;

const editavel = () => cenarios.find((c) => c.nome === cenario)?.editavel ?? false;
const mesAtual = () => meses[Number($<HTMLInputElement>("#mes").value)];
const dataAtual = () => fimDoMes(mesAtual());

function abrirAba(nome: string): void {
  document.querySelectorAll<HTMLButtonElement>("aside nav button").forEach((b) => b.classList.toggle("ativa", b.dataset.aba === nome));
  document.querySelectorAll<HTMLElement>(".aba").forEach((a) => (a.hidden = a.id !== `aba-${nome}`));
}
document.querySelectorAll<HTMLButtonElement>("aside nav button").forEach((b) =>
  b.addEventListener("click", () => abrirAba(b.dataset.aba!)),
);

function ligarLinks(el: HTMLElement): void {
  el.querySelectorAll<HTMLElement>("[data-ent]").forEach((a) =>
    a.addEventListener("click", () => abrirFicha(a.dataset.ent!, a.dataset.id!)),
  );
}

// ---------- legenda ----------
$("#legenda").innerHTML = `
  <div><span class="q" style="border-color:#1f3b57"></span>500 kV <span class="q" style="border-color:#3f6e8c"></span>345 <span class="q" style="border-color:#7a95a8"></span>230</div>
  <div>${Object.entries({ EOL: "eólica", UFV: "solar", UHE: "hidro", UTE: "térmica", MMGD: "MMGD" })
    .map(([k, v]) => `<span class="b" style="background:${COR_FONTE[k]}"></span>${v}`)
    .join(" ")}</div>
  <div><span class="l" style="background:${COR_ESTADO.novo}"></span>entrou <span class="l" style="background:${COR_ESTADO.saiu}"></span>saiu <span class="l" style="background:${COR_ESTADO.mudou}"></span>mudou <span class="l tr"></span>previsto</div>`;

// ---------- carregamento do mapa ----------
async function atualizar(): Promise<void> {
  const meu = ++pedido;
  const mes = mesAtual();
  const data = fimDoMes(mes);
  const kvs = $<HTMLSelectElement>("#kv").value;
  const kv = kvs ? Number(kvs) : undefined;
  const sub = $<HTMLSelectElement>("#sub").value || undefined;
  const futuro = meta.data_base && data > meta.data_base;
  $("#mes-rotulo").innerHTML = `<b>${rotuloMes(mes)}</b><br><small>${futuro ? "projeção" : "situação"} em ${fmtData(data)}</small>`;

  const comparar = $<HTMLInputElement>("#cmp-on").checked;
  const mesCmp = $<HTMLSelectElement>("#cmp-mes").value;
  try {
    const [cfg, evs, cmp] = await Promise.all([
      api.configuracao(cenario, data, kv, sub),
      api.eventos(cenario, `${mes}-01`, data),
      comparar && mesCmp && mesCmp !== mes
        ? Promise.all([api.configuracao(cenario, fimDoMes(mesCmp), kv, sub), api.diferenca(cenario, fimDoMes(mesCmp), data)])
        : Promise.resolve(null),
    ]);
    if (meu !== pedido) return; // chegou uma resposta mais nova

    const semMmgd = !$<HTMLInputElement>("#mmgd").checked;
    const filtraU = (c: Configuracao) => (semMmgd ? c.usinas.filter((u) => u.fonte_energia !== "MMGD") : c.usinas);
    const estado = new Map<string, Estado>();
    const cena: Cena = { barras: [...cfg.barras], ramos: [...cfg.ramos], usinas: filtraU(cfg), estado };
    if (cmp) {
      const [cfgA, dif] = cmp;
      marcarDiferenca(cena, { ...cfgA, usinas: filtraU(cfgA) }, dif);
    } else {
      for (const e of evs) if (e.acao === "entra") estado.set(e.entidade_id, "novo");
      for (const e of evs) if (e.acao === "altera") estado.set(e.entidade_id, "mudou");
    }
    mapa.carregar(cena);
    mostrarEventos(evs, mes);
    mostrarResumo(cfg, cmp ? cmp[1] : null, mesCmp);
  } catch (err) {
    aviso(String(err), true);
  }
}

function marcarDiferenca(cena: Cena, cfgA: Configuracao, dif: Diferenca): void {
  const pega = (t: string) => dif[t] as { entraram: string[]; sairam: string[]; alterados: { id: string }[] };
  for (const t of ["barras", "ramos", "usinas"]) {
    const d = pega(t);
    d.entraram.forEach((id) => cena.estado.set(id, "novo"));
    d.alterados.forEach((x) => cena.estado.set(x.id, "mudou"));
    d.sairam.forEach((id) => cena.estado.set(id, "saiu"));
  }
  const saiu = (id: string) => cena.estado.get(id) === "saiu";
  cena.barras.push(...cfgA.barras.filter((b) => saiu(b.barra_id)));
  cena.ramos.push(...cfgA.ramos.filter((r) => saiu(r.ramo_id)));
  cena.usinas.push(...cfgA.usinas.filter((u) => saiu(u.usina_id)));
}

function mostrarResumo(cfg: Configuracao, dif: Diferenca | null, mesCmp: string): void {
  const capPor = new Map<string, number>();
  for (const u of cfg.usinas) capPor.set(u.fonte_energia, (capPor.get(u.fonte_energia) ?? 0) + (u.cap_mw ?? 0));
  const cap = [...capPor.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([f, v]) => `${f} ${num(v / 1000, 1)} GW`)
    .join(" · ");
  const previstos = cfg.ramos.filter((r) => r.confianca !== "realizado").length + cfg.usinas.filter((u) => u.confianca !== "realizado").length;
  let txt = `<b>${esc(cenario)}</b> · ${cfg.barras.length} barras · ${cfg.ramos.length} ramos · ${cfg.usinas.length} usinas<br>${cap || "sem usinas"}`;
  if (previstos) txt += `<br>${previstos} elemento(s) previstos, não realizados`;
  if (dif) {
    const n = (t: string, k: "entraram" | "sairam") => ((dif[t] as Record<string, unknown[]>)[k] ?? []).length;
    txt += `<br>desde ${rotuloMes(mesCmp)}: +${n("ramos", "entraram")}/−${n("ramos", "sairam")} ramos, +${n("usinas", "entraram")}/−${n("usinas", "sairam")} usinas`;
  }
  $("#resumo").innerHTML = txt;
}

function mostrarEventos(evs: Evento[], mes: string): void {
  const el = $("#aba-eventos");
  if (!evs.length) {
    el.innerHTML = `<p class="vazio">Nenhum evento em ${rotuloMes(mes)} no cenário ${esc(cenario)}.</p>`;
    return;
  }
  el.innerHTML = `<h3>${evs.length} evento(s) em ${rotuloMes(mes)}</h3>` + evs.map(htmlEvento).join("");
  ligarLinks(el);
}

function htmlEvento(e: Evento): string {
  const campos = e.campos
    ? `<div class="campos">${Object.entries(e.campos)
        .map(([c, [a, b]]) => `${esc(c)}: ${esc(a)} → ${esc(b)}`)
        .join("<br>")}</div>`
    : "";
  return `<div class="evento conf-${esc(e.confianca)}">
    <div><span class="data">${fmtData(e.data)}</span> <span class="acao">${esc(e.acao)}</span>
    <a data-ent="${esc(e.entidade)}" data-id="${esc(e.entidade_id)}">${esc(e.entidade)} ${esc(e.entidade_id)}</a></div>
    ${campos}${e.nota ? `<div class="nota">${esc(e.nota)}</div>` : ""}
    <div class="fonte">${esc(e.fonte)} · ${esc(e.confianca)}</div></div>`;
}

// ---------- ficha ----------
const CAMPOS_FICHA: Record<string, string[]> = {
  barras: ["nome", "num_pwf", "kv", "subestacao", "uf", "submercado", "area", "carga_leve_mw", "carga_media_mw", "carga_pesada_mw"],
  ramos: ["nome", "tipo", "de", "para", "circuito", "x_pct", "cap_normal_mva", "cap_emerg_mva", "estado_base"],
  usinas: ["nome", "cod_deck", "fonte_energia", "barra_id", "cap_mw", "flag_corte", "ponto_conexao_ons", "grupo_eixo"],
  restricoes: ["nome", "tipo", "num_deck", "io_ons", "formula_texto"],
};
// campos que o formulário deixa alterar, por entidade
const CAMPOS_EDITAVEIS: Record<string, string[]> = {
  barras: ["carga_leve_mw", "carga_media_mw", "carga_pesada_mw", "nome"],
  ramos: ["cap_normal_mva", "cap_emerg_mva", "x_pct", "estado_base", "nome"],
  usinas: ["cap_mw", "flag_corte", "barra_id", "nome"],
  restricoes: ["nome"],
};

async function abrirFicha(tipo: string, id: string): Promise<void> {
  abrirAba("ficha");
  const el = $("#aba-ficha");
  el.innerHTML = `<p class="vazio">carregando…</p>`;
  try {
    const f: Ficha = await api.entidade(cenario, tipo, id);
    el.innerHTML = htmlFicha(f) + htmlAcoesFicha(f);
    ligarLinks(el);
    ligarAcoesFicha(el, f);
  } catch (err) {
    el.innerHTML = `<p class="erro">${esc(err)}</p>`;
  }
}

function htmlFicha(f: Ficha): string {
  const campos = CAMPOS_FICHA[f.tipo] ?? [];
  const versoes = f.versoes
    .map(
      (v) => `<table class="kv conf-${esc(v.confianca)}"><caption>${fmtData(String(v.vigente_de))} → ${v.vigente_ate ? fmtData(String(v.vigente_ate)) : "em diante"}
      <small>${esc(v.fonte)} · ${esc(v.confianca)}</small></caption>
      ${campos
        .filter((c) => v[c] !== null && v[c] !== undefined)
        .map((c) => `<tr><th>${esc(c)}</th><td>${typeof v[c] === "number" ? num(v[c], 3) : esc(v[c])}</td></tr>`)
        .join("")}</table>`,
    )
    .join("");
  const rel = Object.entries(f.relacionados)
    .map(
      ([t, ids]) =>
        `<h4>${esc(t)} (${ids.length})</h4><div class="rel">${ids
          .map((i) => `<a data-ent="${esc(t)}" data-id="${esc(i)}">${esc(i)}</a>`)
          .join(" ")}</div>`,
    )
    .join("");
  const evs = f.eventos.length ? `<h4>Eventos</h4>${f.eventos.map(htmlEvento).join("")}` : "";
  return `<h3>${esc(f.tipo)} ${esc(f.id)}</h3><p class="sub">${f.versoes.length} versão(ões) no cenário ${esc(cenario)}</p>${versoes}${rel}${evs}`;
}

function htmlAcoesFicha(f: Ficha): string {
  if (!editavel())
    return `<p class="sub dica-edicao">Para mudar este elemento numa data, escolha ou crie um cenário (o caso base não muda).</p>`;
  const opts = (CAMPOS_EDITAVEIS[f.tipo] ?? []).map((c) => `<option>${c}</option>`).join("");
  return `<form class="acao-ficha" id="f-altera">
      <h4>Alterar neste cenário</h4>
      <div class="linha"><select name="campo">${opts}</select><input name="valor" placeholder="novo valor" required /></div>
      <div class="linha"><label>a partir de <input type="date" name="data" value="${dataAtual()}" required /></label><button>alterar</button></div>
    </form>
    <form class="acao-ficha" id="f-sai">
      <h4>Retirar neste cenário</h4>
      <div class="linha"><label>a partir de <input type="date" name="data" value="${dataAtual()}" required /></label><button>retirar</button></div>
    </form>`;
}

function ligarAcoesFicha(el: HTMLElement, f: Ficha): void {
  const fa = el.querySelector<HTMLFormElement>("#f-altera");
  fa?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(fa);
    const campo = String(fd.get("campo"));
    const bruto = String(fd.get("valor")).trim();
    const n = Number(bruto.replace(",", "."));
    const valor = bruto !== "" && !Number.isNaN(n) && !["nome", "barra_id", "estado_base"].includes(campo) ? n : bruto;
    await registrar([{ data: String(fd.get("data")), acao: "altera", entidade: f.tipo, entidade_id: f.id, campos: { [campo]: valor } }], () =>
      abrirFicha(f.tipo, f.id),
    );
  });
  const fs = el.querySelector<HTMLFormElement>("#f-sai");
  fs?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(fs);
    await registrar([{ data: String(fd.get("data")), acao: "sai", entidade: f.tipo, entidade_id: f.id }], () => abrirFicha(f.tipo, f.id));
  });
}

async function registrar(eventos: Partial<EventoCenario>[], depois?: () => void): Promise<boolean> {
  try {
    const r = await api.adicionarEventos(cenario, eventos);
    aviso(`${r.length} evento(s) adicionados ao cenário ${cenario}`);
    await recarregarCenario();
    depois?.();
    return true;
  } catch (err) {
    aviso(String(err), true);
    return false;
  }
}

function htmlAlvo(a: Alvo): string {
  if (a.tipo === "usina")
    return `<b>${esc(a.usina.nome)}</b><br>${esc(a.usina.fonte_energia)} · ${num(a.usina.cap_mw)} MW${a.usina.confianca !== "realizado" ? ` · ${esc(a.usina.confianca)}` : ""}`;
  if (a.tipo === "subestacao") {
    const cap = a.usinas.reduce((s, u) => s + (u.cap_mw ?? 0), 0);
    return `<b>${esc(a.nome)}</b><br>${a.barras.map((b) => `${b.kv} kV`).join(", ")}<br>${a.usinas.length} usinas · ${num(cap)} MW`;
  }
  return a.ramos
    .map((r) => `<b>${esc(r.nome ?? r.ramo_id)}</b> · ${num(r.cap_normal_mva)} MVA${r.confianca !== "realizado" ? ` · ${esc(r.confianca)}` : ""}`)
    .join("<br>");
}

mapa.onPasso = (alvo, x, y) => {
  const d = $("#dica");
  if (!alvo) {
    d.hidden = true;
    return;
  }
  d.hidden = false;
  d.innerHTML = htmlAlvo(alvo);
  d.style.left = `${x + 14}px`;
  d.style.top = `${y + 14}px`;
};

mapa.onClique = (alvo) => {
  if (!alvo) return;
  if (alvo.tipo === "usina") return void abrirFicha("usinas", alvo.usina.usina_id);
  if (alvo.tipo === "ramo" && alvo.ramos.length === 1) return void abrirFicha("ramos", alvo.ramos[0].ramo_id);
  abrirAba("ficha");
  const el = $("#aba-ficha");
  if (alvo.tipo === "ramo") {
    el.innerHTML =
      `<h3>${alvo.ramos.length} ramos em paralelo</h3>` +
      alvo.ramos.map((r) => `<div><a data-ent="ramos" data-id="${esc(r.ramo_id)}">${esc(r.nome ?? r.ramo_id)}</a></div>`).join("");
  } else {
    const usarBarra = editavel()
      ? `<p><button class="sec" id="usar-de">usar como origem de linha nova</button> <button class="sec" id="usar-usina">ligar usina nova aqui</button></p>`
      : "";
    el.innerHTML = `<h3>Subestação ${esc(alvo.nome)}</h3>${usarBarra}
      <h4>Barras</h4>${alvo.barras.map((b) => `<div><a data-ent="barras" data-id="${esc(b.barra_id)}">${esc(b.nome)}</a> · ${b.kv} kV</div>`).join("")}
      <h4>Usinas (${alvo.usinas.length})</h4>${alvo.usinas
        .map((u) => `<div><a data-ent="usinas" data-id="${esc(u.usina_id)}">${esc(u.nome)}</a> · ${esc(u.fonte_energia)} · ${num(u.cap_mw)} MW</div>`)
        .join("")}`;
    const barraPrincipal = [...alvo.barras].sort((a, b) => b.kv - a.kv)[0];
    el.querySelector("#usar-de")?.addEventListener("click", () => preencher("#fl-de", barraPrincipal.barra_id));
    el.querySelector("#usar-usina")?.addEventListener("click", () => preencher("#fu-barra", barraPrincipal.barra_id));
  }
  ligarLinks(el);
};

function preencher(sel: string, valor: string): void {
  abrirAba("cenario");
  const i = document.querySelector<HTMLInputElement>(sel);
  if (i) {
    (i.closest("details") as HTMLDetailsElement | null)?.setAttribute("open", "");
    i.value = valor;
    i.focus();
  }
}

// ---------- aba do cenário ----------
function htmlEventoCenario(e: EventoCenario, proprio: boolean): string {
  const nome = (e.registro?.nome as string) ?? e.entidade_id ?? "";
  const detalheTxt = e.campos
    ? Object.entries(e.campos).map(([c, v]) => `${esc(c)} → ${esc(v)}`).join(", ")
    : e.acao === "entra"
      ? esc(nome)
      : "";
  return `<div class="evento conf-${esc(e.confianca)}">
    <div><span class="data">${fmtData(e.data)}</span> <span class="acao">${esc(e.acao)}</span>
    <a data-ent="${esc(e.entidade)}" data-id="${esc(e.entidade_id ?? "")}">${esc(e.entidade)} ${esc(e.entidade_id ?? "")}</a>
    ${proprio ? `<button class="x" data-rm="${esc(e.evento_id)}" title="remover este evento">×</button>` : ""}</div>
    ${detalheTxt ? `<div class="campos">${detalheTxt}</div>` : ""}${e.nota ? `<div class="nota">${esc(e.nota)}</div>` : ""}
    <div class="fonte">${esc(e.fonte)} · ${esc(e.confianca)}${e.cenario ? ` · herdado de ${esc(e.cenario)}` : ""}</div></div>`;
}

const OPCOES_CONF = `<option>previsto</option><option>contratado</option><option>especulativo</option>`;

function mostrarCenario(): void {
  const el = $("#aba-cenario");
  const novo = `<details class="caixa" ${cenarios.length <= 1 ? "open" : ""}><summary>Novo cenário</summary>
    <form id="f-cenario">
      <div class="linha"><input name="nome" placeholder="nome (ex.: expansao-parpel)" pattern="[a-z0-9][a-z0-9_\\-]*" required /></div>
      <div class="linha"><input name="descricao" placeholder="descrição" /></div>
      <div class="linha"><label>por cima de <select name="pai">${cenarios.map((c) => `<option ${c.nome === cenario ? "selected" : ""}>${esc(c.nome)}</option>`).join("")}</select></label><button>criar</button></div>
    </form></details>`;
  if (!editavel() || !detalhe) {
    el.innerHTML = `<h3>Caso base</h3>
      <p class="sub">O caso base é a foto do deck de ${meta.data_base ? fmtData(meta.data_base) : "?"} e não muda. Para projetar ou testar uma mudança (uma linha nova, uma usina, uma retirada), crie um cenário: ele guarda só os eventos, com data, e é composto por cima do caso base.</p>${novo}`;
    ligarFormCenario();
    return;
  }
  const d = detalhe;
  el.innerHTML = `<h3>Cenário ${esc(d.nome)}</h3>
    <p class="sub">${esc(d.descricao || "sem descrição")}<br>camadas: base → ${d.cadeia.map(esc).join(" → ")} · horizonte ${fmtData(d.horizonte)}</p>

    <details class="caixa" open><summary>Linha nova</summary>
    <form id="f-linha">
      <div class="linha"><input id="fl-de" name="de" list="lista-barras" placeholder="de (barra ou nº)" required /><input name="para" list="lista-barras" placeholder="para" required /></div>
      <div class="linha"><select name="tipo"><option>linha</option><option>trafo</option><option value="elo_cc">elo CC</option></select>
        <input name="cap" type="number" step="any" placeholder="cap. MVA" /><input name="x" type="number" step="any" placeholder="x %" /></div>
      <div class="linha"><input name="nome" placeholder="nome (ex.: LT 500 kV A – B C2)" /></div>
      <div class="linha"><label>entra em <input type="date" name="data" value="${dataAtual()}" required /></label><select name="confianca">${OPCOES_CONF}</select><button>adicionar</button></div>
    </form></details>

    <details class="caixa"><summary>Usina nova</summary>
    <form id="f-usina">
      <div class="linha"><input id="fu-barra" name="barra" list="lista-barras" placeholder="barra (ou nº)" required />
        <select name="fonte_energia"><option>UFV</option><option>EOL</option><option>UHE</option><option>UTE</option><option>MMGD</option><option>BIO</option></select></div>
      <div class="linha"><input name="nome" placeholder="nome" required /><input name="cap" type="number" step="any" placeholder="MW" required /></div>
      <div class="linha"><label>entra em <input type="date" name="data" value="${dataAtual()}" required /></label><select name="confianca">${OPCOES_CONF}</select><button>adicionar</button></div>
    </form></details>

    <details class="caixa"><summary>Importar CSV</summary>
    <form id="f-csv">
      <div class="linha"><select name="tipo"><option value="linhas">linhas</option><option value="usinas">usinas</option></select>
        <input type="file" name="arq" accept=".csv,text/csv" /></div>
      <textarea name="texto" rows="4" placeholder="ou cole o CSV aqui"></textarea>
      <p class="sub">linhas: data,de,para,tipo,circuitos,x_pct,cap_normal_mva,nome,fonte,confianca,nota<br>usinas: data,nome,fonte_energia,barra,cap_mw,flag_corte,fonte,confianca,nota</p>
      <div class="linha"><button>importar</button></div>
    </form></details>

    <h4>Eventos deste cenário (${d.eventos.length})</h4>
    ${d.eventos.length ? [...d.eventos].sort((a, b) => a.data.localeCompare(b.data)).map((e) => htmlEventoCenario(e, true)).join("") : `<p class="vazio">Nenhum ainda.</p>`}
    ${d.herdados.length ? `<h4>Herdados (${d.herdados.length})</h4>${d.herdados.map((e) => htmlEventoCenario(e, false)).join("")}` : ""}
    <p><button class="perigo" id="apagar-cenario">apagar cenário</button></p>
    ${novo}`;
  ligarLinks(el);
  ligarFormCenario();

  const barra = (v: FormDataEntryValue | null) => {
    const s = String(v ?? "").trim().split(" ")[0];
    return /^\d+$/.test(s) ? `B${s}` : s;
  };
  const fl = $<HTMLFormElement>("#f-linha");
  fl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(fl);
    const cap = fd.get("cap") ? Number(fd.get("cap")) : null;
    const x = fd.get("x") ? Number(fd.get("x")) : null;
    await registrar([
      {
        data: String(fd.get("data")),
        acao: "entra",
        entidade: "ramos",
        confianca: String(fd.get("confianca")),
        registro: { de: barra(fd.get("de")), para: barra(fd.get("para")), tipo: String(fd.get("tipo")), cap_normal_mva: cap, x_pct: x, nome: String(fd.get("nome") || "") || null, estado_base: "ligado" },
      },
    ]);
  });
  const fu = $<HTMLFormElement>("#f-usina");
  fu.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(fu);
    await registrar([
      {
        data: String(fd.get("data")),
        acao: "entra",
        entidade: "usinas",
        confianca: String(fd.get("confianca")),
        registro: { nome: String(fd.get("nome")), fonte_energia: String(fd.get("fonte_energia")), barra_id: barra(fd.get("barra")), cap_mw: Number(fd.get("cap")), flag_corte: 1 },
      },
    ]);
  });
  const fc = $<HTMLFormElement>("#f-csv");
  fc.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(fc);
    const arq = fd.get("arq") as File | null;
    const texto = arq && arq.size ? await arq.text() : String(fd.get("texto") || "");
    if (!texto.trim()) return aviso("CSV vazio", true);
    try {
      const r = await api.importar(cenario, fd.get("tipo") as "linhas" | "usinas", texto);
      aviso(`${r.length} evento(s) importados`);
      await recarregarCenario();
    } catch (err) {
      aviso(String(err), true);
    }
  });
  el.querySelectorAll<HTMLButtonElement>("[data-rm]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api.removerEvento(cenario, b.dataset.rm!);
        aviso("evento removido");
        await recarregarCenario();
      } catch (err) {
        aviso(String(err), true);
      }
    }),
  );
  $("#apagar-cenario").addEventListener("click", async () => {
    if (!window.confirm(`Apagar o cenário ${cenario}? Os eventos dele se perdem.`)) return;
    try {
      await api.apagarCenario(cenario);
      await trocarCenario("base");
    } catch (err) {
      aviso(String(err), true);
    }
  });
  el.querySelectorAll<HTMLInputElement>("input[list=lista-barras]").forEach((i) => i.addEventListener("input", () => buscarBarras(i.value)));
}

function ligarFormCenario(): void {
  const f = document.querySelector<HTMLFormElement>("#f-cenario");
  f?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(f);
    const pai = String(fd.get("pai"));
    try {
      const c = await api.criarCenario(String(fd.get("nome")), String(fd.get("descricao") || ""), pai === "base" ? null : pai);
      await trocarCenario(c.nome);
      abrirAba("cenario");
    } catch (err) {
      aviso(String(err), true);
    }
  });
}

let buscaTimer = 0;
function buscarBarras(q: string): void {
  window.clearTimeout(buscaTimer);
  buscaTimer = window.setTimeout(async () => {
    try {
      const r = await api.barras(cenario, dataAtual(), q);
      $("#lista-barras").innerHTML = r
        .map((b) => `<option value="${esc(b.barra_id)}">${esc(b.nome)} · ${b.kv} kV · ${esc(b.subestacao)}</option>`)
        .join("");
    } catch {
      /* busca é só ajuda */
    }
  }, 200);
}

// ---------- linha do tempo ----------
function montarLinhaTempo(manterMes?: string): void {
  meses = listaMeses(meta.inicio!, meta.fim!);
  const slider = $<HTMLInputElement>("#mes");
  slider.max = String(meses.length - 1);
  const alvo = manterMes && meses.includes(manterMes) ? manterMes : (meta.data_base ?? meta.inicio!).slice(0, 7);
  slider.value = String(Math.max(0, meses.indexOf(alvo)));
  const porMes = new Map(meta.eventos_por_mes.map((x) => [x.mes, x]));
  const max = Math.max(1, ...meta.eventos_por_mes.map((x) => x.n));
  const pos = (i: number) => (meses.length > 1 ? (100 * i) / (meses.length - 1) : 0);
  $("#marcas").innerHTML = meses
    .map((m, i) => {
      const e = porMes.get(m);
      const h = e ? 6 + (18 * e.n) / max : 0;
      const tipo = e && e.n_realizado < e.n ? "previsto" : "realizado";
      const ano = m.endsWith("-01") || i === 0 ? `<span class="ano">${m.slice(0, 4)}</span>` : "";
      return `<div class="marca-mes" style="left:${pos(i)}%">${e ? `<i class="${tipo}" style="height:${h}px" title="${e.n} evento(s) em ${rotuloMes(m)}"></i>` : ""}${ano}</div>`;
    })
    .join("");
  const ib = meta.data_base ? meses.indexOf(meta.data_base.slice(0, 7)) : -1;
  const mb = $("#marca-base");
  mb.hidden = ib < 0;
  if (ib >= 0) mb.style.left = `calc(8px + (100% - 16px) * ${pos(ib) / 100})`;
  const cmpSel = $<HTMLSelectElement>("#cmp-mes");
  const anterior = cmpSel.value;
  cmpSel.innerHTML = meses.map((m) => `<option value="${m}">${rotuloMes(m)}</option>`).join("");
  cmpSel.value = meses.includes(anterior) ? anterior : (meta.data_base ?? meses[0]).slice(0, 7);
}

let animando: number | null = null;
$("#play").addEventListener("click", () => {
  const slider = $<HTMLInputElement>("#mes");
  if (animando !== null) {
    clearInterval(animando);
    animando = null;
    $("#play").textContent = "▶";
    return;
  }
  $("#play").textContent = "❚❚";
  animando = window.setInterval(() => {
    const v = Number(slider.value) + 1;
    if (v > Number(slider.max)) {
      clearInterval(animando!);
      animando = null;
      $("#play").textContent = "▶";
      return;
    }
    slider.value = String(v);
    void atualizar();
  }, 900);
});

for (const id of ["#mes", "#kv", "#sub", "#mmgd", "#cmp-on", "#cmp-mes"]) $(id).addEventListener("input", () => void atualizar());
$("#mes").addEventListener("change", () => {
  if (editavel()) mostrarCenario(); // atualiza as datas padrão dos formulários
});
$("#ajustar").addEventListener("click", () => mapa.ajustar());
$("#cenario").addEventListener("change", () => void trocarCenario($<HTMLSelectElement>("#cenario").value));

// ---------- validação e sobre ----------
async function mostrarValidacao(): Promise<void> {
  const v = await api.validacao(cenario);
  $("#aba-validacao").innerHTML =
    `<h3>${v.erros} erro(s), ${v.avisos} aviso(s)</h3><p class="sub">cenário ${esc(cenario)}</p>` +
    (v.problemas.length
      ? v.problemas.slice(0, 200).map((p) => `<div class="prob ${esc(p.severidade)}"><b>${esc(p.checagem)}</b> · ${esc(p.tabela)}<br>${esc(p.detalhe)}</div>`).join("")
      : `<p class="vazio">Nenhum problema encontrado.</p>`);
}

function mostrarSobre(): void {
  const avisos = (meta.info.avisos as string[] | undefined) ?? [];
  $("#aba-sobre").innerHTML = `
    <h3>${esc(meta.info.nome ?? "Cadastro")}</h3>
    <p class="sub">${esc(meta.pasta)}<br>caso base em ${meta.data_base ? fmtData(meta.data_base) : "?"}</p>
    <table class="kv">${Object.entries(meta.linhas).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${num(v)}</td></tr>`).join("")}</table>
    ${avisos.length ? `<h4>Avisos do cadastro</h4><ul>${avisos.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
    <p class="sub">Posições indicativas: cada subestação parte do centro da UF e um layout de molas a afasta das vizinhas. Não é geografia exata.</p>
    <p class="sub">kurt-app ${esc(meta.versao_app)}</p>`;
}

// ---------- cenários ----------
async function recarregarCenario(): Promise<void> {
  const mes = meses.length ? mesAtual() : undefined;
  [meta, cenarios] = await Promise.all([api.meta(cenario), api.cenarios()]);
  detalhe = editavel() ? await api.cenario(cenario) : null;
  $<HTMLSelectElement>("#cenario").innerHTML = cenarios
    .map((c) => `<option value="${esc(c.nome)}" ${c.nome === cenario ? "selected" : ""}>${esc(c.nome)}${c.n_eventos ? ` (${c.n_eventos})` : ""}</option>`)
    .join("");
  montarLinhaTempo(mes);
  mostrarCenario();
  await atualizar();
  void mostrarValidacao();
}

async function trocarCenario(nome: string): Promise<void> {
  cenario = nome;
  const u = new URL(location.href);
  if (nome === "base") u.searchParams.delete("cenario");
  else u.searchParams.set("cenario", nome);
  history.replaceState(null, "", u);
  try {
    await recarregarCenario();
  } catch (err) {
    aviso(String(err), true);
    if (nome !== "base") await trocarCenario("base");
  }
}

// ---------- início ----------
(async () => {
  try {
    cenarios = await api.cenarios();
    if (!cenarios.some((c) => c.nome === cenario)) cenario = "base";
    meta = await api.meta(cenario);
    $("#cadastro-nome").textContent = meta.info.nome ? `· ${meta.info.nome}` : "";
    if (!meta.inicio || !meta.fim) {
      $("#aba-eventos").innerHTML = `<p class="erro">Cadastro vazio.</p>`;
      return;
    }
    mostrarSobre();
    await trocarCenario(cenario);
    buscarBarras("");
  } catch (err) {
    $("#aba-eventos").innerHTML = `<p class="erro">Falha ao falar com a API: ${esc(err)}</p>`;
  }
})();

export {};
