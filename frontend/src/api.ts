import type { Configuracao, Diferenca, Evento, Ficha, Meta, Validacao } from "./tipos";

async function get<T>(caminho: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const url = q.toString() ? `${caminho}?${q}` : caminho;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} em ${url}: ${await r.text()}`);
  return (await r.json()) as T;
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  configuracao: (data: string, kv_min?: number, submercado?: string) =>
    get<Configuracao>("/api/configuracao", { data, kv_min, submercado }),
  eventos: (de?: string, ate?: string) => get<Evento[]>("/api/eventos", { de, ate }),
  entidade: (tipo: string, id: string) => get<Ficha>(`/api/entidade/${tipo}/${encodeURIComponent(id)}`),
  diferenca: (a: string, b: string) => get<Diferenca>("/api/diferenca", { a, b }),
  validacao: () => get<Validacao>("/api/validacao"),
};
