import "./estilo.css";
import { api } from "./api";
import { COR_ESTADO, COR_FONTE, Mapa, type Alvo, type Cena, type Estado } from "./mapa";
import type { Configuracao, Diferenca, Evento, Ficha, Meta } from "./tipos";

// ---------- utilidades ----------
const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const fmtData = (iso: string) => iso.split("-").reverse().join("/");
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const rotuloMes = (m: string) => `${MESES[Number(m.slice(5, 7)) - 1]}/${m.slice(2, 4)}`;
const fimDoMes = (m: string) => {
  const [a, mm] = m.split("-").map(Number);
  const d = new Date(Date.UTC(a, mm, 0));
  return d.toISOString().slice(0, 10);
};

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

// ---------- estrutura da página ----------
$("#app").innerHTML = `
<header>
  <div class="marca">kurt-app <span id="cadastro-nome"></span></div>
  <div class="controles">
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
    <label class="comparar"><input type="checkbox" id="cmp-on" /> comparar com <select id="cmp-mes"></select></label>
    <button id="ajustar" title="Enquadrar o mapa">enquadrar</button>
  </div>
</header>
<section class="linha-tempo">
  <button id="play" title="Animar mês a mês">▶</button>
  <div class="trilho">
    <div id="marcas"></div>
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
  </div>
  <aside>
    <nav>
      <button data-aba="eventos" class="ativa">Eventos</button>
      <button data-aba="ficha">Ficha</button>
      <button data-aba="validacao">Validação</button>
      <button data-aba="sobre">Sobre</button>
    </nav>
    <div id="aba-eventos" class="aba"></div>
    <div id="aba-ficha" class="aba" hidden><p class="vazio">Clique numa subestação, linha ou usina.</p></div>
    <div id="aba-validacao" class="aba" hidden></div>
    <div id="aba-sobre" class="aba" hidden></div>
  </aside>
</main>`;

const mapa = new Mapa($<HTMLCanvasElement>("#mapa"));
let meta: Meta;
let meses: string[] = [];
let pedido = 0;

function abrirAba(nome: string): void {
  document.querySelectorAll<HTMLButtonElement>("aside nav button").forEach((b) => b.classList.toggle("ativa", b.dataset.aba === nome));
  document.querySelectorAll<HTMLElement>(".aba").forEach((a) => (a.hidden = a.id !== `aba-${nome}`));
}
document.querySelectorAll<HTMLButtonElement>("aside nav button").forEach((b) =>
  b.addEventListener("click", () => abrirAba(b.dataset.aba!)),
);

// ---------- legenda ----------
$("#legenda").innerHTML = `
  <div><span class="q" style="border-color:#1f3b57"></span>500 kV <span class="q" style="border-color:#3f6e8c"></span>345 <span class="q" style="border-color:#7a95a8"></span>230</div>
  <div>${Object.entries({ EOL: "eólica", UFV: "solar", UHE: "hidro", UTE: "térmica" })
    .map(([k, v]) => `<span class="b" style="background:${COR_FONTE[k]}"></span>${v}`)
    .join(" ")}</div>
  <div><span class="l" style="background:${COR_ESTADO.novo}"></span>entrou <span class="l" style="background:${COR_ESTADO.saiu}"></span>saiu <span class="l" style="background:${COR_ESTADO.mudou}"></span>mudou <span class="l tr"></span>não realizado</div>`;

// ---------- carregamento ----------
async function atualizar(): Promise<void> {
  const meu = ++pedido;
  const mes = meses[Number($<HTMLInputElement>("#mes").value)];
  const data = fimDoMes(mes);
  const kvs = $<HTMLSelectElement>("#kv").value;
  const kv = kvs ? Number(kvs) : undefined;
  const sub = $<HTMLSelectElement>("#sub").value || undefined;
  $("#mes-rotulo").innerHTML = `<b>${rotuloMes(mes)}</b><br><small>situação em ${fmtData(data)}</small>`;

  const comparar = $<HTMLInputElement>("#cmp-on").checked;
  const mesCmp = $<HTMLSelectElement>("#cmp-mes").value;
  const [cfg, evs, cmp] = await Promise.all([
    api.configuracao(data, kv, sub),
    api.eventos(`${mes}-01`, data),
    comparar && mesCmp && mesCmp !== mes
      ? Promise.all([api.configuracao(fimDoMes(mesCmp), kv, sub), api.diferenca(fimDoMes(mesCmp), data)])
      : Promise.resolve(null),
  ]);
  if (meu !== pedido) return; // chegou uma resposta mais nova

  const estado = new Map<string, Estado>();
  const cena: Cena = { barras: [...cfg.barras], ramos: [...cfg.ramos], usinas: [...cfg.usinas], estado };
  if (cmp) {
    const [cfgA, dif] = cmp;
    marcarDiferenca(cena, cfgA, dif);
  } else {
    // sem comparação, destaca o que entrou no próprio mês
    for (const e of evs) if (e.acao === "entra") estado.set(e.entidade_id, "novo");
    for (const e of evs) if (e.acao === "altera") estado.set(e.entidade_id, "mudou");
  }
  mapa.carregar(cena);
  mostrarEventos(evs, mes);
  mostrarResumo(cfg, cmp ? cmp[1] : null, mesCmp);
}

function marcarDiferenca(cena: Cena, cfgA: Configuracao, dif: Diferenca): void {
  const pega = (t: string) => dif[t] as { entraram: string[]; sairam: string[]; alterados: { id: string }[] };
  for (const t of ["barras", "ramos", "usinas"]) {
    const d = pega(t);
    d.entraram.forEach((id) => cena.estado.set(id, "novo"));
    d.alterados.forEach((x) => cena.estado.set(x.id, "mudou"));
    d.sairam.forEach((id) => cena.estado.set(id, "saiu"));
  }
  // o que saiu não está na configuração atual: traz da data de comparação
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
    .map(([f, v]) => `${f} ${(v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} GW`)
    .join(" · ");
  let txt = `${cfg.barras.length} barras · ${cfg.ramos.length} ramos · ${cfg.usinas.length} usinas<br>${cap || "sem usinas"}`;
  if (dif) {
    const n = (t: string, k: "entraram" | "sairam") => ((dif[t] as Record<string, unknown[]>)[k] ?? []).length;
    txt += `<br>desde ${rotuloMes(mesCmp)}: +${n("ramos", "entraram")}/−${n("ramos", "sairam")} ramos, +${n("usinas", "entraram")}/−${n("usinas", "sairam")} usinas`;
  }
  $("#resumo").innerHTML = txt;
}

function mostrarEventos(evs: Evento[], mes: string): void {
  const el = $("#aba-eventos");
  if (!evs.length) {
    el.innerHTML = `<p class="vazio">Nenhum evento em ${rotuloMes(mes)}.</p>`;
    return;
  }
  el.innerHTML = `<h3>${evs.length} evento(s) em ${rotuloMes(mes)}</h3>` + evs.map(htmlEvento).join("");
  el.querySelectorAll<HTMLElement>("[data-ent]").forEach((a) =>
    a.addEventListener("click", () => abrirFicha(a.dataset.ent!, a.dataset.id!)),
  );
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
  barras: ["nome", "num_pwf", "kv", "subestacao", "uf", "submercado", "area", "carga_media_mw"],
  ramos: ["nome", "tipo", "de", "para", "circuito", "x_pct", "cap_normal_mva", "estado_base"],
  usinas: ["nome", "fonte_energia", "barra_id", "cap_mw", "flag_corte", "ponto_conexao_ons", "grupo_eixo"],
  restricoes: ["nome", "tipo", "num_deck", "io_ons", "formula_texto"],
};

async function abrirFicha(tipo: string, id: string): Promise<void> {
  abrirAba("ficha");
  const el = $("#aba-ficha");
  el.innerHTML = `<p class="vazio">carregando…</p>`;
  try {
    const f: Ficha = await api.entidade(tipo, id);
    el.innerHTML = htmlFicha(f);
    el.querySelectorAll<HTMLElement>("[data-ent]").forEach((a) =>
      a.addEventListener("click", () => abrirFicha(a.dataset.ent!, a.dataset.id!)),
    );
  } catch (err) {
    el.innerHTML = `<p class="erro">${esc(err)}</p>`;
  }
}

function htmlFicha(f: Ficha): string {
  const campos = CAMPOS_FICHA[f.tipo] ?? [];
  const versoes = f.versoes
    .map(
      (v) => `<table class="kv"><caption>${fmtData(String(v.vigente_de))} → ${v.vigente_ate ? fmtData(String(v.vigente_ate)) : "hoje"}
      <small>${esc(v.fonte)} · ${esc(v.confianca)}</small></caption>
      ${campos
        .filter((c) => v[c] !== null && v[c] !== undefined)
        .map((c) => `<tr><th>${esc(c)}</th><td>${esc(v[c])}</td></tr>`)
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
  return `<h3>${esc(f.tipo)} ${esc(f.id)}</h3><p class="sub">${f.versoes.length} versão(ões)</p>${versoes}${rel}${evs}`;
}

function htmlAlvo(a: Alvo): string {
  if (a.tipo === "usina")
    return `<b>${esc(a.usina.nome)}</b><br>${esc(a.usina.fonte_energia)} · ${esc(a.usina.cap_mw)} MW`;
  if (a.tipo === "subestacao") {
    const cap = a.usinas.reduce((s, u) => s + (u.cap_mw ?? 0), 0);
    return `<b>${esc(a.nome)}</b><br>${a.barras.map((b) => `${b.kv} kV`).join(", ")}<br>${a.usinas.length} usinas · ${Math.round(cap)} MW`;
  }
  return a.ramos.map((r) => `<b>${esc(r.nome ?? r.ramo_id)}</b> · ${esc(r.cap_normal_mva ?? "?")} MVA`).join("<br>");
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
    el.innerHTML = `<h3>${alvo.ramos.length} ramos em paralelo</h3>` +
      alvo.ramos.map((r) => `<div><a data-ent="ramos" data-id="${esc(r.ramo_id)}">${esc(r.nome ?? r.ramo_id)}</a></div>`).join("");
  } else {
    el.innerHTML = `<h3>Subestação ${esc(alvo.nome)}</h3>
      <h4>Barras</h4>${alvo.barras.map((b) => `<div><a data-ent="barras" data-id="${esc(b.barra_id)}">${esc(b.nome)}</a> · ${b.kv} kV</div>`).join("")}
      <h4>Usinas (${alvo.usinas.length})</h4>${alvo.usinas
        .map((u) => `<div><a data-ent="usinas" data-id="${esc(u.usina_id)}">${esc(u.nome)}</a> · ${esc(u.fonte_energia)} · ${esc(u.cap_mw)} MW</div>`)
        .join("")}`;
  }
  el.querySelectorAll<HTMLElement>("[data-ent]").forEach((a) =>
    a.addEventListener("click", () => abrirFicha(a.dataset.ent!, a.dataset.id!)),
  );
};

// ---------- linha do tempo ----------
function montarLinhaTempo(): void {
  const slider = $<HTMLInputElement>("#mes");
  slider.max = String(meses.length - 1);
  slider.value = "0";
  const porMes = new Map(meta.eventos_por_mes.map((x) => [x.mes, x]));
  const max = Math.max(1, ...meta.eventos_por_mes.map((x) => x.n));
  $("#marcas").innerHTML = meses
    .map((m, i) => {
      const e = porMes.get(m);
      const left = meses.length > 1 ? (100 * i) / (meses.length - 1) : 0;
      const h = e ? 6 + (18 * e.n) / max : 0;
      const tipo = e && e.n_realizado < e.n ? "previsto" : "realizado";
      const ano = m.endsWith("-01") || i === 0 ? `<span class="ano">${m.slice(0, 4)}</span>` : "";
      return `<div class="marca-mes" style="left:${left}%">${e ? `<i class="${tipo}" style="height:${h}px" title="${e.n} evento(s) em ${rotuloMes(m)}"></i>` : ""}${ano}</div>`;
    })
    .join("");
  $<HTMLSelectElement>("#cmp-mes").innerHTML = meses.map((m) => `<option value="${m}">${rotuloMes(m)}</option>`).join("");
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

for (const id of ["#mes", "#kv", "#sub", "#cmp-on", "#cmp-mes"]) $(id).addEventListener("input", () => void atualizar());
$("#ajustar").addEventListener("click", () => mapa.ajustar());

// ---------- validação e sobre ----------
async function mostrarValidacao(): Promise<void> {
  const v = await api.validacao();
  $("#aba-validacao").innerHTML =
    `<h3>${v.erros} erro(s), ${v.avisos} aviso(s)</h3>` +
    (v.problemas.length
      ? v.problemas.map((p) => `<div class="prob ${esc(p.severidade)}"><b>${esc(p.checagem)}</b> · ${esc(p.tabela)}<br>${esc(p.detalhe)}</div>`).join("")
      : `<p class="vazio">Nenhum problema encontrado.</p>`);
}

function mostrarSobre(): void {
  const avisos = meta.info.avisos ?? [];
  $("#aba-sobre").innerHTML = `
    <h3>${esc(meta.info.nome ?? "Cadastro")}</h3>
    <p class="sub">${esc(meta.pasta)}</p>
    <table class="kv">${Object.entries(meta.linhas).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>
    ${avisos.length ? `<h4>Avisos do cadastro</h4><ul>${avisos.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
    <p class="sub">Posições indicativas: cada subestação parte do centro da UF e um layout de molas a afasta das vizinhas. Não é geografia exata.</p>
    <p class="sub">kurt-app ${esc(meta.versao_app)}</p>`;
}

// ---------- início ----------
(async () => {
  try {
    meta = await api.meta();
    $("#cadastro-nome").textContent = meta.info.nome ? `· ${meta.info.nome}` : "";
    if (!meta.inicio || !meta.fim) {
      $("#aba-eventos").innerHTML = `<p class="erro">Cadastro vazio.</p>`;
      return;
    }
    meses = listaMeses(meta.inicio, meta.fim);
    montarLinhaTempo();
    mostrarSobre();
    await atualizar();
    void mostrarValidacao();
  } catch (err) {
    $("#aba-eventos").innerHTML = `<p class="erro">Falha ao falar com a API: ${esc(err)}</p>`;
  }
})();

export {};
