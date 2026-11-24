import type { BarraBusca, CenarioDetalhe, CenarioResumo, Configuracao, Diferenca, Evento, EventoCenario, Ficha, Insumo, Meta, Simulacao, Validacao } from "./tipos";

type Params = Record<string, string | number | undefined>;

async function pedir<T>(metodo: string, caminho: string, params: Params = {}, corpo?: unknown, texto = false, sinal?: AbortSignal): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const url = q.toString() ? `${caminho}?${q}` : caminho;
  const init: RequestInit = { method: metodo, signal: sinal };
  if (corpo !== undefined) {
    init.body = texto ? String(corpo) : JSON.stringify(corpo);
    init.headers = { "content-type": texto ? "text/plain" : "application/json" };
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = await r.text();
    try {
      msg = JSON.parse(msg).detail ?? msg;
    } catch {
      /* texto puro */
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return (r.status === 204 ? null : await r.json()) as T;
}

const get = <T>(c: string, p: Params = {}, sinal?: AbortSignal) => pedir<T>("GET", c, p, undefined, false, sinal);

export type Ufs = Record<string, { aneis: [number, number][][]; centro: [number, number] | null }>;

export const api = {
  ufs: () => get<Ufs>("/api/ufs"),
  meta: (cenario: string) => get<Meta>("/api/meta", { cenario }),
  configuracao: (cenario: string, data: string, kv_min?: number, submercado?: string, sinal?: AbortSignal) =>
    get<Configuracao>("/api/configuracao", { cenario, data, kv_min, submercado }, sinal),
  eventos: (cenario: string, de?: string, ate?: string, sinal?: AbortSignal) => get<Evento[]>("/api/eventos", { cenario, de, ate }, sinal),
  entidade: (cenario: string, tipo: string, id: string) =>
    get<Ficha>(`/api/entidade/${tipo}/${encodeURIComponent(id)}`, { cenario }),
  diferenca: (cenario: string, a: string, b: string, sinal?: AbortSignal) => get<Diferenca>("/api/diferenca", { cenario, a, b }, sinal),
  validacao: (cenario: string) => get<Validacao>("/api/validacao", { cenario }),
  barras: (cenario: string, data: string, q: string) => get<BarraBusca[]>("/api/barras", { cenario, data, q }),

  // cenários de simulação
  insumos: () => get<Insumo[]>("/api/insumos"),
  simulacoes: () => get<Simulacao[]>("/api/simulacoes"),
  simulacao: (nome: string) => get<Simulacao>(`/api/simulacoes/${nome}`),
  criarSimulacao: (dados: { nome: string; descricao?: string; inicio?: string; fim?: string; caso_rede?: string }) =>
    pedir<Simulacao>("POST", "/api/simulacoes", {}, dados),
  atualizarSimulacao: (nome: string, mudancas: Partial<Pick<Simulacao, "descricao" | "inicio" | "fim" | "insumos">>) =>
    pedir<Simulacao>("PATCH", `/api/simulacoes/${nome}`, {}, mudancas),
  apagarSimulacao: (nome: string) => pedir<null>("DELETE", `/api/simulacoes/${nome}`),

  // casos de rede (camadas de eventos sobre o caso base; a API ainda os chama de cenários)
  cenarios: () => get<CenarioResumo[]>("/api/cenarios"),
  cenario: (nome: string) => get<CenarioDetalhe>(`/api/cenarios/${nome}`),
  criarCenario: (nome: string, descricao: string, pai: string | null) =>
    pedir<CenarioDetalhe>("POST", "/api/cenarios", {}, { nome, descricao, pai }),
  apagarCenario: (nome: string) => pedir<null>("DELETE", `/api/cenarios/${nome}`),
  adicionarEventos: (nome: string, eventos: Partial<EventoCenario>[]) =>
    pedir<EventoCenario[]>("POST", `/api/cenarios/${nome}/eventos`, {}, eventos),
  removerEvento: (nome: string, id: string) =>
    pedir<null>("DELETE", `/api/cenarios/${nome}/eventos/${encodeURIComponent(id)}`),
  importar: (nome: string, tipo: "linhas" | "usinas", csv: string) =>
    pedir<EventoCenario[]>("POST", `/api/cenarios/${nome}/importar`, { tipo }, csv, true),
};
