// Mapa indicativo em canvas: subestações, ramos entre subestações e usinas.
// As posições vêm de pos_x/pos_y (graus aproximados, não geografia exata).

import type { Barra, Ramo, Usina } from "./tipos";

export type Estado = "normal" | "novo" | "saiu" | "mudou";

export interface Cena {
  barras: Barra[];
  ramos: Ramo[];
  usinas: Usina[];
  estado: Map<string, Estado>; // por id de barra, ramo ou usina
}

export type Alvo =
  | { tipo: "subestacao"; nome: string; barras: Barra[]; usinas: Usina[] }
  | { tipo: "ramo"; ramos: Ramo[] }
  | { tipo: "usina"; usina: Usina };

interface No {
  nome: string;
  x: number;
  y: number;
  kv: number;
  barras: Barra[];
  usinas: Usina[];
  estado: Estado;
}

interface Aresta {
  a: No;
  b: No;
  ramos: Ramo[];
  kv: number;
  cap: number;
  estado: Estado;
  tracejada: boolean;
  desvio: number; // deslocamento lateral, em pixels, quando há grupos paralelos
}

interface PontoUsina {
  u: Usina;
  x: number;
  y: number;
  r: number;
  estado: Estado;
}

export const COR_FONTE: Record<string, string> = {
  EOL: "#1a9e8f",
  UFV: "#e0a106",
  MMGD: "#c9a227",
  UHE: "#2f6fb3",
  PCH: "#5b8fd1",
  CGH: "#5b8fd1",
  UTE: "#a94438",
  BIO: "#6b8e23",
};
export const COR_ESTADO: Record<Estado, string> = {
  normal: "",
  novo: "#2e9d4b",
  saiu: "#c4372d",
  mudou: "#7b4fc4",
};

function corKv(kv: number): string {
  if (kv >= 500) return "#1f3b57";
  if (kv >= 345) return "#3f6e8c";
  if (kv >= 230) return "#7a95a8";
  return "#b5c2cc";
}

function pior(a: Estado, b: Estado): Estado {
  const ordem: Estado[] = ["normal", "mudou", "novo", "saiu"];
  return ordem.indexOf(b) > ordem.indexOf(a) ? b : a;
}

export class Mapa {
  private ctx: CanvasRenderingContext2D;
  private nos: No[] = [];
  private arestas: Aresta[] = [];
  private pontos: PontoUsina[] = [];
  private escala = 60; // pixels por grau
  private cx = 0;
  private cy = 0;
  private arrastando: { x: number; y: number; cx: number; cy: number; moveu: boolean } | null = null;
  private ajustado = false;
  onClique: (alvo: Alvo | null) => void = () => {};
  onPasso: (alvo: Alvo | null, x: number, y: number) => void = () => {};

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d indisponível");
    this.ctx = ctx;
    new ResizeObserver(() => this.desenhar()).observe(canvas);
    canvas.addEventListener("wheel", (e) => this.zoom(e), { passive: false });
    canvas.addEventListener("mousedown", (e) => {
      this.arrastando = { x: e.offsetX, y: e.offsetY, cx: this.cx, cy: this.cy, moveu: false };
    });
    window.addEventListener("mouseup", (e) => {
      if (this.arrastando && !this.arrastando.moveu && e.target === canvas) {
        this.onClique(this.alvoEm(e.offsetX, e.offsetY));
      }
      this.arrastando = null;
    });
    canvas.addEventListener("mousemove", (e) => {
      const a = this.arrastando;
      if (a) {
        const dx = e.offsetX - a.x;
        const dy = e.offsetY - a.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) a.moveu = true;
        this.cx = a.cx - dx / this.escala;
        this.cy = a.cy - dy / this.escala;
        this.desenhar();
      } else {
        this.onPasso(this.alvoEm(e.offsetX, e.offsetY), e.clientX, e.clientY);
      }
    });
  }

  carregar(cena: Cena): void {
    const est = (id: string) => cena.estado.get(id) ?? "normal";
    const porSub = new Map<string, No>();
    const subDaBarra = new Map<string, No>();
    for (const b of cena.barras) {
      let no = porSub.get(b.subestacao);
      if (!no) {
        no = { nome: b.subestacao, x: b.pos_x ?? 0, y: -(b.pos_y ?? 0), kv: b.kv, barras: [], usinas: [], estado: "normal" };
        porSub.set(b.subestacao, no);
      }
      no.kv = Math.max(no.kv, b.kv);
      no.barras.push(b);
      no.estado = pior(no.estado, est(b.barra_id));
      subDaBarra.set(b.barra_id, no);
    }
    const kvBarra = new Map(cena.barras.map((b) => [b.barra_id, b.kv] as [string, number]));
    const porPar = new Map<string, Aresta>();
    for (const r of cena.ramos) {
      const a = subDaBarra.get(r.de);
      const b = subDaBarra.get(r.para);
      if (!a || !b || a === b) continue; // trafo interno à subestação não vira aresta
      const [p, q] = a.nome < b.nome ? [a, b] : [b, a];
      // ramos do mesmo par só se juntam se tiverem o mesmo estado e a mesma confiança;
      // assim um circuito novo aparece ao lado do existente, e não por cima
      const k = `${p.nome}|${q.nome}|${est(r.ramo_id)}|${r.confianca === "realizado"}`;
      let ar = porPar.get(k);
      if (!ar) {
        ar = { a: p, b: q, ramos: [], kv: 0, cap: 0, estado: est(r.ramo_id), tracejada: r.confianca !== "realizado", desvio: 0 };
        porPar.set(k, ar);
      }
      ar.ramos.push(r);
      const kvr = Math.min(kvBarra.get(r.de) ?? 0, kvBarra.get(r.para) ?? 0);
      ar.kv = Math.max(ar.kv, kvr);
      ar.cap += r.cap_normal_mva ?? 0;
    }
    this.nos = [...porSub.values()];
    this.arestas = [...porPar.values()];
    const grupos = new Map<string, Aresta[]>();
    for (const ar of this.arestas) {
      const k = `${ar.a.nome}|${ar.b.nome}`;
      grupos.set(k, [...(grupos.get(k) ?? []), ar]);
    }
    for (const g of grupos.values()) g.forEach((ar, i) => (ar.desvio = (i - (g.length - 1) / 2) * 5));
    this.pontos = [];
    for (const u of cena.usinas) {
      const no = subDaBarra.get(u.barra_id);
      if (!no) continue;
      no.usinas.push(u);
    }
    for (const no of this.nos) {
      const n = no.usinas.length;
      no.usinas.forEach((u, i) => {
        const ang = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
        const anel = 0.09 + 0.035 * Math.floor(i / 12);
        this.pontos.push({
          u,
          x: no.x + Math.cos(ang) * anel,
          y: no.y + Math.sin(ang) * anel,
          r: Math.max(2, Math.sqrt(u.cap_mw ?? 10) / 3.2),
          estado: est(u.usina_id),
        });
      });
    }
    if (!this.ajustado && this.nos.length) {
      this.ajustar();
      this.ajustado = true;
    }
    this.desenhar();
  }

  ajustar(): void {
    if (!this.nos.length) return;
    const xs = this.nos.map((n) => n.x);
    const ys = this.nos.map((n) => n.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    this.cx = (x0 + x1) / 2;
    this.cy = (y0 + y1) / 2;
    const w = this.canvas.clientWidth || 800;
    const h = this.canvas.clientHeight || 600;
    this.escala = Math.min(w / Math.max(x1 - x0 + 0.6, 0.5), h / Math.max(y1 - y0 + 0.6, 0.5), 2000);
    this.desenhar();
  }

  private tela(x: number, y: number): [number, number] {
    return [
      (x - this.cx) * this.escala + this.canvas.clientWidth / 2,
      (y - this.cy) * this.escala + this.canvas.clientHeight / 2,
    ];
  }

  private segmento(a: Aresta): [number, number, number, number] {
    const [x1, y1] = this.tela(a.a.x, a.a.y);
    const [x2, y2] = this.tela(a.b.x, a.b.y);
    if (!a.desvio) return [x1, y1, x2, y2];
    const L = Math.hypot(x2 - x1, y2 - y1) || 1;
    const nx = (-(y2 - y1) / L) * a.desvio;
    const ny = ((x2 - x1) / L) * a.desvio;
    return [x1 + nx, y1 + ny, x2 + nx, y2 + ny];
  }

  private zoom(e: WheelEvent): void {
    e.preventDefault();
    const f = Math.exp(-e.deltaY * 0.0015);
    const wx = (e.offsetX - this.canvas.clientWidth / 2) / this.escala + this.cx;
    const wy = (e.offsetY - this.canvas.clientHeight / 2) / this.escala + this.cy;
    this.escala = Math.min(Math.max(this.escala * f, 5), 20000);
    this.cx = wx - (e.offsetX - this.canvas.clientWidth / 2) / this.escala;
    this.cy = wy - (e.offsetY - this.canvas.clientHeight / 2) / this.escala;
    this.desenhar();
  }

  desenhar(): void {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (c.width !== w * dpr || c.height !== h * dpr) {
      c.width = w * dpr;
      c.height = h * dpr;
    }
    const g = this.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    // ramos
    for (const a of this.arestas) {
      const [x1, y1, x2, y2] = this.segmento(a);
      g.beginPath();
      g.moveTo(x1, y1);
      g.lineTo(x2, y2);
      g.lineWidth = Math.min(1 + a.cap / 1200, 7);
      g.strokeStyle = a.estado !== "normal" ? COR_ESTADO[a.estado] : corKv(a.kv);
      g.setLineDash(a.tracejada ? [6, 4] : []);
      g.globalAlpha = a.estado === "saiu" ? 0.6 : 1;
      g.stroke();
    }
    g.setLineDash([]);
    g.globalAlpha = 1;

    // usinas
    const mostrarUsinas = this.escala > 25;
    if (mostrarUsinas) {
      for (const p of this.pontos) {
        const [x, y] = this.tela(p.x, p.y);
        const r = p.r * Math.min(Math.max(this.escala / 250, 0.6), 2.2);
        g.beginPath();
        g.arc(x, y, r, 0, 2 * Math.PI);
        g.fillStyle = COR_FONTE[p.u.fonte_energia] ?? "#888";
        g.globalAlpha = p.estado === "saiu" ? 0.35 : 0.85;
        g.fill();
        g.globalAlpha = 1;
        if (p.estado !== "normal" || p.u.confianca !== "realizado") {
          g.lineWidth = 2;
          g.strokeStyle = p.estado !== "normal" ? COR_ESTADO[p.estado] : "#555";
          g.setLineDash(p.u.confianca !== "realizado" ? [3, 2] : []);
          g.stroke();
          g.setLineDash([]);
        }
      }
    }

    // subestações
    for (const n of this.nos) {
      const [x, y] = this.tela(n.x, n.y);
      const r = n.kv >= 500 ? 5 : n.kv >= 345 ? 4 : 3;
      g.beginPath();
      g.rect(x - r, y - r, 2 * r, 2 * r);
      g.fillStyle = "#fff";
      g.fill();
      g.lineWidth = n.estado !== "normal" ? 3 : 1.6;
      g.strokeStyle = n.estado !== "normal" ? COR_ESTADO[n.estado] : corKv(n.kv);
      g.stroke();
    }

    // rótulos
    if (this.escala > 90) {
      g.font = "11px system-ui, sans-serif";
      g.fillStyle = "#223";
      g.textBaseline = "middle";
      for (const n of this.nos) {
        const [x, y] = this.tela(n.x, n.y);
        g.fillText(n.nome, x + 8, y - 8);
      }
    }
  }

  private alvoEm(px: number, py: number): Alvo | null {
    if (this.escala > 25) {
      for (const p of [...this.pontos].reverse()) {
        const [x, y] = this.tela(p.x, p.y);
        const r = p.r * Math.min(Math.max(this.escala / 250, 0.6), 2.2) + 2;
        if ((x - px) ** 2 + (y - py) ** 2 <= r * r) return { tipo: "usina", usina: p.u };
      }
    }
    for (const n of this.nos) {
      const [x, y] = this.tela(n.x, n.y);
      if (Math.abs(x - px) <= 8 && Math.abs(y - py) <= 8)
        return { tipo: "subestacao", nome: n.nome, barras: n.barras, usinas: n.usinas };
    }
    let melhor: Aresta | null = null;
    let dmin = 6;
    for (const a of this.arestas) {
      const [x1, y1, x2, y2] = this.segmento(a);
      const vx = x2 - x1;
      const vy = y2 - y1;
      const t = Math.max(0, Math.min(1, ((px - x1) * vx + (py - y1) * vy) / (vx * vx + vy * vy || 1)));
      const d = Math.hypot(px - (x1 + t * vx), py - (y1 + t * vy));
      if (d < dmin) {
        dmin = d;
        melhor = a;
      }
    }
    return melhor ? { tipo: "ramo", ramos: melhor.ramos } : null;
  }
}
