// Tipos das respostas da API (espelham kurt_app/esquema.py).

export interface Vigencia {
  vigente_de: string;
  vigente_ate: string | null;
  fonte: string;
  confianca: string;
}

export interface Barra extends Vigencia {
  barra_id: string;
  num_pwf: number | null;
  nome: string;
  kv: number;
  area: number | null;
  submercado: string | null;
  subestacao: string;
  uf: string | null;
  pos_x: number | null;
  pos_y: number | null;
  carga_leve_mw: number | null;
  carga_media_mw: number | null;
  carga_pesada_mw: number | null;
  elevatoria: boolean | null;
}

export interface Ramo extends Vigencia {
  ramo_id: string;
  de: string;
  para: string;
  circuito: number | null;
  tipo: string;
  nome: string | null;
  x_pct: number | null;
  cap_normal_mva: number | null;
  cap_emerg_mva: number | null;
  estado_base: string | null;
}

export interface Usina extends Vigencia {
  usina_id: string;
  cod_deck: string | null;
  nome: string;
  fonte_energia: string;
  barra_id: string;
  submercado: string | null;
  cap_mw: number | null;
  flag_corte: number | null;
  ids_ons: unknown;
  ponto_conexao_ons: string | null;
  grupo_eixo: string | null;
}

export interface Restricao extends Vigencia {
  restr_id: string;
  tipo: string;
  num_deck: number | null;
  nome: string;
  termos: unknown;
  limites: unknown;
  faixas_lpp: unknown;
  io_ons: string | null;
  formula_texto: string | null;
}

export interface Evento {
  evento_id: string;
  data: string;
  acao: string;
  entidade: string;
  entidade_id: string;
  campos: Record<string, [unknown, unknown]> | null;
  fonte: string;
  confianca: string;
  nota: string | null;
}

export interface Configuracao {
  data: string;
  barras: Barra[];
  ramos: Ramo[];
  usinas: Usina[];
  restricoes: Restricao[];
}

export interface Meta {
  versao_app: string;
  pasta: string;
  inicio: string | null;
  fim: string | null;
  data_base: string | null;
  cenario: string;
  eventos_por_mes: { mes: string; n: number; n_realizado: number }[];
  linhas: Record<string, number>;
  info: { nome?: string; avisos?: string[] } & Record<string, unknown>;
}

export interface Ficha {
  tipo: string;
  id: string;
  versoes: Record<string, unknown>[];
  eventos: Evento[];
  relacionados: Record<string, string[]>;
}

export interface Diferenca {
  a: string;
  b: string;
  [tipo: string]: { entraram: string[]; sairam: string[]; alterados: { id: string; campos: Record<string, [unknown, unknown]> }[] } | string;
}

export interface Validacao {
  erros: number;
  avisos: number;
  problemas: { severidade: string; checagem: string; tabela: string; detalhe: string }[];
}

export interface BarraBusca {
  barra_id: string;
  nome: string;
  kv: number;
  subestacao: string;
  uf: string | null;
  submercado: string | null;
}

export interface CenarioResumo {
  nome: string;
  descricao: string;
  pai: string | null;
  horizonte?: string;
  n_eventos: number;
  editavel: boolean;
}

export interface EventoCenario {
  evento_id: string;
  data: string;
  acao: "entra" | "sai" | "altera";
  entidade: string;
  entidade_id?: string;
  registro?: Record<string, unknown>;
  campos?: Record<string, unknown>;
  fonte: string;
  confianca: string;
  nota?: string | null;
  cenario?: string;
}

export interface CenarioDetalhe {
  nome: string;
  descricao: string;
  pai: string | null;
  horizonte: string;
  eventos: EventoCenario[];
  herdados: EventoCenario[];
  cadeia: string[];
}
