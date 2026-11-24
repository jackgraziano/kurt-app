import "./estilo.css";
import { api } from "./api";
import type { CenarioResumo, Insumo, Meta, Simulacao } from "./tipos";
import { $, aviso, esc, fimDoMes, fmtData, listaMeses, rotuloMes } from "./util";

// Casca do app: tela principal (lista e criação de cenários) e a tela de um
// cenário, com uma aba por insumo da simulação. A aba "Rede elétrica" é o mapa
// (rede.ts); as outras ainda são esqueleto.
//
// Rotas (hash): #/  ·  #/c/<cenário>  ·  #/c/<cenário>/<aba>

$("#app").innerHTML = `
<header class="topo">
  <a class="marca" href="#/">kurt-app <span id="cadastro-nome"></span></a>
  <span id="trilha"></span>
</header>
<section id="tela-inicio" class="tela-rolavel" hidden></section>
<section id="tela-cenario" hidden>
  <nav id="abas-cenario"></nav>
  <div id="conteudo-cenario" class="tela-rolavel"></div>
  <div id="tela-rede" hidden></div>
</section>
<div id="aviso" hidden></div>`;

let meta: Meta;
let insumos: Insumo[] = [];
let rede: typeof import("./rede") | null = null;
let redeAberta = ""; // chave do que a aba Rede mostra, para não recarregar à toa

const ABA_RESUMO = "resumo";
const meses = (s: Simulacao) => listaMeses(s.inicio, s.fim).length;
const horizonte = (s: Simulacao) =>
  `${rotuloMes(s.inicio.slice(0, 7))} a ${rotuloMes(s.fim.slice(0, 7))} · ${meses(s)} ${meses(s) === 1 ? "mês" : "meses"}`;
const casoRede = (s: Simulacao) => s.insumos.rede?.caso ?? "base";

function mesSeguinte(iso: string, n = 1): string {
  const [a, m] = iso.split("-").map(Number);
  const d = new Date(Date.UTC(a, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

// ---------- tela principal ----------
async function telaInicio(): Promise<void> {
  mostrarTela("inicio");
  $("#trilha").textContent = "";
  const el = $("#tela-inicio");
  const [sims, casos] = await Promise.all([api.simulacoes(), api.cenarios()]);
  const base = meta.data_base ?? new Date().toISOString().slice(0, 10);
  const ini = mesSeguinte(base);
  const fim = mesSeguinte(ini, 11);
  el.innerHTML = `
    <div class="inicio">
      <h2>Cenários</h2>
      <p class="sub">Um cenário reúne o horizonte e os insumos de uma simulação do Kurt: rede elétrica, carga, renováveis, hidro e térmicas.
      O caso base é o deck de ${meta.data_base ? fmtData(meta.data_base) : "?"}.</p>
      <div class="grade-inicio">
        <form id="f-simulacao" class="cartao">
          <h3>Novo cenário</h3>
          <label>Nome <input name="nome" required pattern="[a-z0-9][a-z0-9_\\-]*" placeholder="ex.: dez26-expansao" /></label>
          <label>Descrição <input name="descricao" placeholder="opcional" /></label>
          <div class="par">
            <label>Início <input name="inicio" type="month" required value="${ini}" /></label>
            <label>Fim <input name="fim" type="month" required value="${fim}" /></label>
          </div>
          <label>Caso de rede <select name="caso_rede">${opcoesCasos(casos, "base")}</select></label>
          <p class="sub" id="f-horizonte"></p>
          <button>criar cenário</button>
        </form>
        <div class="lista-sim">
          ${sims.length ? sims.map(cartaoSimulacao).join("") : `<p class="vazio">Nenhum cenário ainda. Crie o primeiro ao lado.</p>`}
        </div>
      </div>
    </div>`;
  const f = el.querySelector<HTMLFormElement>("#f-simulacao")!;
  const resumoHorizonte = () => {
    const fd = new FormData(f);
    const i = String(fd.get("inicio"));
    const e = String(fd.get("fim"));
    $("#f-horizonte").textContent =
      /^\d{4}-\d{2}$/.test(i) && /^\d{4}-\d{2}$/.test(e) && e >= i
        ? `${listaMeses(i, e).length} meses: ${rotuloMes(i)} a ${rotuloMes(e)}`
        : "Use AAAA-MM; o fim não pode vir antes do início.";
  };
  f.addEventListener("input", resumoHorizonte);
  resumoHorizonte();
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(f);
    try {
      const s = await api.criarSimulacao({
        nome: String(fd.get("nome")),
        descricao: String(fd.get("descricao") || ""),
        inicio: `${fd.get("inicio")}-01`,
        fim: fimDoMes(String(fd.get("fim"))),
        caso_rede: String(fd.get("caso_rede")),
      });
      location.hash = `#/c/${s.nome}`;
    } catch (err) {
      aviso(String(err), true);
    }
  });
}

function opcoesCasos(casos: CenarioResumo[], atual: string): string {
  return casos
    .map(
      (c) =>
        `<option value="${esc(c.nome)}" ${c.nome === atual ? "selected" : ""}>${esc(c.nome)}${c.n_eventos ? ` (${c.n_eventos} eventos)` : ""}</option>`,
    )
    .join("");
}

function cartaoSimulacao(s: Simulacao): string {
  const prontos = insumos.filter((i) => configurado(s, i)).length;
  return `<a class="cartao sim" href="#/c/${esc(s.nome)}">
    <div class="sim-topo"><b>${esc(s.nome)}</b><span class="sub">${horizonte(s)}</span></div>
    ${s.descricao ? `<div>${esc(s.descricao)}</div>` : ""}
    <div class="sub">rede: ${esc(casoRede(s))} · ${prontos}/${insumos.length} insumos definidos · atualizado ${fmtData(s.atualizado_em.slice(0, 10))}</div>
  </a>`;
}

// um insumo está definido quando a aba já tem lógica e guardou alguma escolha
const configurado = (s: Simulacao, i: Insumo) => i.disponivel && Object.keys(s.insumos[i.id] ?? {}).length > 0;

// ---------- tela do cenário ----------
async function telaCenario(nome: string, aba: string): Promise<void> {
  let s: Simulacao;
  try {
    s = await api.simulacao(nome);
  } catch (err) {
    aviso(String(err), true);
    location.hash = "#/";
    return;
  }
  mostrarTela("cenario");
  $("#trilha").innerHTML = `/ <b>${esc(s.nome)}</b> <span class="sub">${horizonte(s)}</span>`;
  const abas = [{ id: ABA_RESUMO, rotulo: "Resumo", disponivel: true }, ...insumos];
  if (!abas.some((a) => a.id === aba)) aba = ABA_RESUMO;
  $("#abas-cenario").innerHTML = abas
    .map(
      (a) =>
        `<a href="#/c/${esc(s.nome)}/${a.id}" class="${a.id === aba ? "ativa" : ""} ${a.disponivel ? "" : "esqueleto"}">${esc(a.rotulo)}</a>`,
    )
    .join("");

  const conteudo = $("#conteudo-cenario");
  const telaRede = $("#tela-rede");
  conteudo.hidden = aba === "rede";
  telaRede.hidden = aba !== "rede";
  if (aba === "rede") return abrirAbaRede(s);
  if (aba === ABA_RESUMO) return mostrarResumo(s);
  const ins = insumos.find((i) => i.id === aba)!;
  conteudo.innerHTML = `<div class="pagina">
    <h2>${esc(ins.rotulo)}</h2>
    <p>${esc(ins.descricao)}</p>
    <p class="vazio">Esta aba ainda não tem configuração. Quando tiver, a escolha feita aqui fica guardada no cenário
    <b>${esc(s.nome)}</b>, para o horizonte ${horizonte(s)}.</p>
  </div>`;
}

async function abrirAbaRede(s: Simulacao): Promise<void> {
  const chave = `${s.nome}|${s.inicio}|${s.fim}|${casoRede(s)}`;
  if (!rede) rede = await import("./rede");
  if (chave !== redeAberta) {
    redeAberta = chave;
    await rede.abrirRede({
      caso: casoRede(s),
      inicio: s.inicio,
      fim: s.fim,
      aoTrocarCaso: async (caso) => {
        try {
          const n = await api.atualizarSimulacao(s.nome, { insumos: { rede: { caso } } });
          redeAberta = `${n.nome}|${n.inicio}|${n.fim}|${caso}`;
          aviso(`cenário ${n.nome} agora usa o caso de rede ${caso}`);
        } catch (err) {
          aviso(String(err), true);
        }
      },
    });
  }
  rede.mostrouRede();
}

async function mostrarResumo(s: Simulacao): Promise<void> {
  const casos = await api.cenarios();
  const linhas = insumos
    .map((i) => {
      const estado = !i.disponivel ? "a fazer" : configurado(s, i) ? "definido" : "a definir";
      const detalhe = i.id === "rede" ? `caso de rede <b>${esc(casoRede(s))}</b>` : esc(i.descricao);
      return `<tr><th><a href="#/c/${esc(s.nome)}/${i.id}">${esc(i.rotulo)}</a></th><td>${detalhe}</td><td><span class="estado ${estado.replace(" ", "-")}">${estado}</span></td></tr>`;
    })
    .join("");
  const el = $("#conteudo-cenario");
  el.innerHTML = `<div class="pagina">
    <h2>${esc(s.nome)}</h2>
    <form id="f-resumo" class="cartao">
      <label>Descrição <input name="descricao" value="${esc(s.descricao)}" /></label>
      <div class="par">
        <label>Início <input name="inicio" type="month" required value="${s.inicio.slice(0, 7)}" /></label>
        <label>Fim <input name="fim" type="month" required value="${s.fim.slice(0, 7)}" /></label>
      </div>
      <label>Caso de rede <select name="caso">${opcoesCasos(casos, casoRede(s))}</select></label>
      <div><button>salvar</button></div>
    </form>
    <h3>Insumos</h3>
    <table class="insumos">${linhas}</table>
    <p class="sub">Criado em ${fmtData(s.criado_em.slice(0, 10))} às ${s.criado_em.slice(11, 16)}.</p>
    <p><button class="perigo" id="apagar-simulacao">apagar cenário</button></p>
  </div>`;
  el.querySelector<HTMLFormElement>("#f-resumo")!.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target as HTMLFormElement);
    try {
      await api.atualizarSimulacao(s.nome, {
        descricao: String(fd.get("descricao") || ""),
        inicio: `${fd.get("inicio")}-01`,
        fim: fimDoMes(String(fd.get("fim"))),
        insumos: { rede: { caso: String(fd.get("caso")) } },
      });
      aviso("cenário salvo");
      void rotear();
    } catch (err) {
      aviso(String(err), true);
    }
  });
  $("#apagar-simulacao").addEventListener("click", async () => {
    if (!window.confirm(`Apagar o cenário ${s.nome}? Os casos de rede usados por ele continuam existindo.`)) return;
    try {
      await api.apagarSimulacao(s.nome);
      location.hash = "#/";
    } catch (err) {
      aviso(String(err), true);
    }
  });
}

// ---------- rotas ----------
function mostrarTela(qual: "inicio" | "cenario"): void {
  $("#tela-inicio").hidden = qual !== "inicio";
  $("#tela-cenario").hidden = qual !== "cenario";
}

async function rotear(): Promise<void> {
  const partes = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (partes[0] === "c" && partes[1]) await telaCenario(partes[1], partes[2] ?? ABA_RESUMO);
    else await telaInicio();
  } catch (err) {
    aviso(String(err), true);
  }
}

(async () => {
  try {
    [meta, insumos] = await Promise.all([api.meta("base"), api.insumos()]);
    $("#cadastro-nome").textContent = meta.info.nome ? `· ${meta.info.nome}` : "";
    window.addEventListener("hashchange", () => void rotear());
    await rotear();
  } catch (err) {
    $("#tela-inicio").hidden = false;
    $("#tela-inicio").innerHTML = `<p class="erro">Falha ao falar com a API: ${esc(err)}</p>`;
  }
})();
