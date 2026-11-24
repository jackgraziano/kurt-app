// Utilidades compartilhadas pelas telas.

export const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
export const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
export const fmtData = (iso: string) => iso.split("-").reverse().join("/");
export const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
export const rotuloMes = (m: string) => `${MESES[Number(m.slice(5, 7)) - 1]}/${m.slice(2, 4)}`;
export const fimDoMes = (m: string) => {
  const [a, mm] = m.split("-").map(Number);
  return new Date(Date.UTC(a, mm, 0)).toISOString().slice(0, 10);
};
export const num = (v: unknown, casas = 0) =>
  typeof v === "number" ? v.toLocaleString("pt-BR", { maximumFractionDigits: casas }) : esc(v);

export function listaMeses(inicio: string, fim: string): string[] {
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

export function aviso(msg: string, erro = false): void {
  const el = $("#aviso");
  el.textContent = msg;
  el.className = erro ? "erro" : "";
  el.hidden = false;
  window.setTimeout(() => (el.hidden = true), erro ? 9000 : 3500);
}

