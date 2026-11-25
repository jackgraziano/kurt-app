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

// cenário de simulação (tela principal e abas de insumos)
export interface Insumo {
  id: string;
  rotulo: string;
  descricao: string;
  disponivel: boolean;
}

export interface Simulacao {
  nome: string;
  descricao: string;
  inicio: string;
  fim: string;
  criado_em: string;
  atualizado_em: string;
  insumos: Record<string, Record<string, unknown>> & { rede?: { caso?: string } };
}

// aba Decks
export interface FonteDecks {
  tipo: "pasta" | "ons";
  caminho: string;
  rotulo?: string;
}

export interface Deck {
  id: string;
  fonte: string;
  caminho: string;
  inicio: string;
  mes: string;
  revisao: number;
}

export interface SemanaDeck {
  inicio: string;
  fim: string;
  mes: string;
  semana: number;
  deck: string | null;
  estagio: number | null;
  repetido: boolean;
}

export interface InsumoDecks {
  fonte: FonteDecks | null;
  so_rv0: boolean;
  semanas: SemanaDeck[];
  decks_disponiveis?: Deck[];
  erro_fonte?: string;
  avisos?: string[];
}

export interface DocInsumo<T> {
  formato: string;
  versao: number;
  insumo: string;
  dados: T;
}

// abas que vêm do DECOMP
export interface SemanaSeries {
  semana: string;
  dias: string[];
  patamar: number[];
  carga: Record<string, number[]>;
  mmgd: Record<string, number[]>;
  ande: number[];
  renovaveis: Record<string, number[]>;
  fator_renovaveis: Record<string, number | null>;
  fator_fora_da_faixa: Record<string, number>;
}

export type Linha = Record<string, string | number | boolean | null>;

export interface DadosAba {
  opcoes: Record<string, unknown>;
  avisos?: string[];
  semanas?: Linha[];
  plano?: { data: string; peso: number; tipo: string | null; cenarios: number }[];
  resumo?: { dias: number; rodadas: number };
  [tabela: string]: unknown;
}

// rodadas do Kurt
export interface RodadaResumo {
  id: string;
  cenario: string;
  criada_em: string;
  terminada_em?: string;
  status: string;
  contagem: { total: number; ok: number; erro: number; rodando: number; "na fila": number; cancelada: number };
}

export interface Quantis {
  p10: number;
  p50: number;
  p90: number;
  media: number;
}

export interface Resultados {
  id: string;
  status: string;
  contagem: RodadaResumo["contagem"];
  cenarios: number;
  unidade: string;
  meses: (Quantis & { mes: string; submercado: string; tipo: string })[];
  usinas: (Quantis & { usina_id: string; submercado: string; fonte: string })[];
}

// sensibilidades (Carga e Renováveis) e energia por dia
export interface RegraNivel {
  fonte: string;
  submercado: string;
  fator: number;
  meses?: string[];
  datas?: string[];
}
export interface Sensibilidade {
  nivel: RegraNivel[];
  forma: Record<string, number[]>;
}
export interface Energia {
  semana: string;
  dias: string[];
  base: Record<string, number[]>;
}
