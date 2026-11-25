import { api } from "./api";
import type { Deck, FonteDecks, InsumoDecks, SemanaDeck, Simulacao } from "./tipos";
import { $, aviso, esc, fmtData, rotuloMes } from "./util";

// Aba Decks: de onde vêm os decks DECOMP do cenário e qual deck (e estágio)
// responde por cada semana operativa do horizonte.

const OUTRA = "__outra__";

/** <select> de fontes configuradas, mais "outra pasta…" e, opcionalmente, "escolher depois". */
export function opcoesFontes(fontes: FonteDecks[], atual: FonteDecks | null, comNenhuma: boolean): string {
  const conhecida = atual && fontes.some((f) => f.caminho === atual.caminho);
  const ops = fontes.map(
    (f, i) =>
      `<option value="${i}" ${atual?.caminho === f.caminho ? "selected" : ""}>${esc(f.rotulo ?? f.caminho)} (${f.tipo === "ons" ? "oficial" : "pasta"})</option>`,
  );
  if (comNenhuma) ops.unshift(`<option value="" ${atual ? "" : "selected"}>escolher depois</option>`);
  ops.push(`<option value="${OUTRA}" ${atual && !conhecida ? "selected" : ""}>outra pasta…</option>`);
  return ops.join("");
}

/** Lê a fonte escolhida no par select + campo de pasta. */
export function fonteEscolhida(fontes: FonteDecks[], valor: string, pasta: string): FonteDecks | null {
  if (valor === OUTRA) return pasta.trim() ? { tipo: "pasta", caminho: pasta.trim() } : null;
  if (valor === "") return null;
  return fontes[Number(valor)] ?? null;
}

/** Mostra o campo de pasta só quando "outra pasta…" está escolhida. */
export function ligarOutraPasta(select: HTMLSelectElement, campo: HTMLElement): void {
  const atualizar = () => (campo.hidden = select.value !== OUTRA);
  select.addEventListener("change", atualizar);
  atualizar();
}

function rotuloDeck(d: Deck | undefined, id: string | null): string {
  if (!id) return "sem deck";
  if (!d) return id;
  return `${d.id} · ${rotuloMes(d.mes)} RV${d.revisao}`;
}

export async function abrirDecks(el: HTMLElement, s: Simulacao, aoMudar: () => Promise<void>): Promise<void> {
  const [doc, fontes] = await Promise.all([api.insumo<InsumoDecks>(s.nome, "decks"), api.fontesDecks()]);
  const d = doc.dados;
  const disponiveis = d.decks_disponiveis ?? [];
  const meses = new Map<string, SemanaDeck[]>();
  for (const x of d.semanas) meses.set(x.mes, [...(meses.get(x.mes) ?? []), x]);
  const pastaAtual = d.fonte && !fontes.some((f) => f.caminho === d.fonte!.caminho) ? d.fonte.caminho : "";

  const linhasMes = [...meses]
    .map(([mes, sems]) => {
      const celulas = sems
        .map((x) => {
          const ops = disponiveis
            .map((k) => `<option value="${esc(k.id)}" ${k.id === x.deck ? "selected" : ""}>${esc(rotuloDeck(k, k.id))}</option>`)
            .join("");
          return `<td class="${x.repetido ? "repetido" : ""}" title="semana ${x.semana} do mês operativo: ${fmtData(x.inicio)} a ${fmtData(x.fim)}${x.repetido ? " · repete um deck anterior" : ""}">
            <div class="sub">${fmtData(x.inicio).slice(0, 5)} a ${fmtData(x.fim).slice(0, 5)}</div>
            <select data-semana="${esc(x.inicio)}">${ops}</select>
            <label class="estagio">est. <input type="number" min="1" max="6" value="${x.estagio ?? ""}" data-estagio="${esc(x.inicio)}" /></label>
          </td>`;
        })
        .join("");
      return `<tr><th>${esc(rotuloMes(mes))}</th>${celulas}</tr>`;
    })
    .join("");

  el.innerHTML = `<div class="pagina larga">
    <h2>Decks DECOMP</h2>
    <p class="sub">Cada semana operativa (sábado a sexta) do horizonte usa um deck DECOMP e um estágio dele. Do deck saem os níveis de carga,
    térmicas, renováveis, vazões e a FCF; a forma horária vem do histórico. Mês sem deck repete o deck anterior (em amarelo).</p>
    <form id="f-decks" class="cartao">
      <div class="par">
        <label>Fonte <select name="fonte">${opcoesFontes(fontes, d.fonte, false)}</select></label>
        <label id="campo-pasta">Pasta na máquina do servidor <input name="pasta" value="${esc(pastaAtual)}" placeholder="/caminho/dos/decks" /></label>
      </div>
      <label class="check"><input type="checkbox" name="so_rv0" ${d.so_rv0 !== false ? "checked" : ""} /> usar só a RV0 de cada mês (projeção)</label>
      <div><button class="primario" name="refazer">salvar fonte e refazer o mapa</button></div>
    </form>
    ${d.erro_fonte ? `<p class="erro">${esc(d.erro_fonte)}</p>` : ""}
    ${(d.avisos ?? []).map((a) => `<p class="alerta">${esc(a)}</p>`).join("")}
    ${
      d.semanas.length
        ? `<h3>Semana a semana</h3>
           <div class="rolagem-x"><table class="semanas-decks">${linhasMes}</table></div>
           <p><button id="salvar-semanas">salvar mudanças nas semanas</button></p>
           <h3>Decks na fonte (${disponiveis.length})</h3>
           <table class="insumos">${disponiveis
             .map((k) => `<tr><th>${esc(k.id)}</th><td>${rotuloMes(k.mes)} RV${k.revisao}, início ${fmtData(k.inicio)}</td><td class="sub">${esc(k.caminho)}</td></tr>`)
             .join("")}</table>`
        : `<p class="vazio">Escolha a fonte dos decks para montar o mapa das semanas.</p>`
    }
  </div>`;

  const f = el.querySelector<HTMLFormElement>("#f-decks")!;
  ligarOutraPasta(f.querySelector<HTMLSelectElement>("select[name=fonte]")!, $("#campo-pasta"));
  const subir = async (dados: Partial<InsumoDecks>, msg: string) => {
    try {
      await api.subirInsumo(s.nome, "decks", { formato: doc.formato, versao: doc.versao, insumo: "decks", dados });
      aviso(msg);
      await aoMudar();
    } catch (err) {
      aviso(String(err), true);
    }
  };
  f.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const fd = new FormData(f);
    const fonte = fonteEscolhida(fontes, String(fd.get("fonte")), String(fd.get("pasta") ?? ""));
    if (!fonte) return aviso("escolha uma fonte ou digite a pasta dos decks", true);
    void subir({ fonte, so_rv0: fd.get("so_rv0") === "on" }, "fonte salva e mapa refeito");
  });
  el.querySelector("#salvar-semanas")?.addEventListener("click", () => {
    const semanas = d.semanas.map((x) => {
      const deck = el.querySelector<HTMLSelectElement>(`select[data-semana="${x.inicio}"]`)!.value;
      const estagio = Number(el.querySelector<HTMLInputElement>(`input[data-estagio="${x.inicio}"]`)!.value);
      return { ...x, deck, estagio, repetido: x.repetido && deck === x.deck };
    });
    void subir({ fonte: d.fonte, so_rv0: d.so_rv0, semanas }, "semanas salvas");
  });
}
