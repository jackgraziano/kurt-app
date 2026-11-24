# kurt-app

Aplicação local para enxergar o sistema físico que o Kurt usa e, depois, rodar projeções de curtailment sobre ele.

Roda na máquina Linux, junto com os decks e o binário do Kurt. Não tem login, nuvem nem deploy.

## O que já faz (v0.1)

- **Cadastro do sistema físico:** cinco tabelas em Parquet (`barras`, `ramos`, `usinas`, `restricoes`, `eventos`), cada registro com vigência `[vigente_de, vigente_ate)`. Qualquer data é um recorte. O esquema está em `kurt_app/esquema.py`.
- **API local** (FastAPI + DuckDB):
  - `GET /api/meta`: período, meses com eventos, contagens;
  - `GET /api/configuracao?data=AAAA-MM-DD&kv_min=&submercado=`: o sistema vigente na data;
  - `GET /api/eventos?de=&ate=&entidade=&confianca=`;
  - `GET /api/entidade/{barras|ramos|usinas|restricoes}/{id}`: versões e eventos de uma entidade;
  - `GET /api/diferenca?a=&b=`: o que entrou, saiu ou mudou entre duas datas;
  - `GET /api/validacao`: checagens de qualidade.
- **Mapa indicativo com linha do tempo:** subestações, ramos e usinas, mês a mês, com comparação entre duas datas, ficha de cada elemento e a lista de eventos do mês. As posições são aproximadas de propósito: cada subestação parte do centro da UF e um layout de molas a afasta das vizinhas.
- **Checagens:** versões sobrepostas, domínios, JSON, referências a barras inexistentes, barras com usina ou carga isoladas e ilhas na rede.

## Cenários: o caso base projetado

O cadastro extraído do deck mais recente é o **caso base** e nunca é alterado. Um cenário é uma camada de eventos datados aplicada sobre ele (ou sobre outro cenário, o pai), em memória:

- `entra`: um ramo, uma usina ou uma barra nova a partir de uma data;
- `sai`: um elemento deixa de existir numa data;
- `altera`: muda campos (capacidade, `x_pct`, `cap_mw`...) a partir de uma data.

Cada evento leva `fonte` e `confianca` (`realizado`, `contratado`, `previsto`, `especulativo`). O mapa desenha tracejado o que não é realizado. A linha do tempo vai até o horizonte do cenário (padrão: caso base + 5 anos); depois da data do caso base os meses aparecem como "projeção".

Os eventos são validados ao entrar: barras referenciadas precisam existir na data, ids não podem repetir, e remover um evento do qual outro depende é recusado. Cada cenário é um JSON em `dados/cenarios/<cadastro>/<nome>.json` (ou `KURT_APP_CENARIOS`), fácil de versionar ou trocar.

Pelo app: aba **Cenário** (linha nova, usina nova, importar CSV, remover evento) e, na ficha de um elemento, "alterar" ou "retirar neste cenário". Pela linha de comando:

```bash
kurt-app cenario criar expansao --cadastro <pasta> --descricao "expansão oficial"
kurt-app cenario importar expansao linhas.csv --cadastro <pasta> --tipo linhas
kurt-app cenario criar sens-lt --cadastro <pasta> --pai expansao
kurt-app cenario listar --cadastro <pasta>
```

Formatos de CSV (cabeçalho obrigatório; barras pelo número do deck):

```
data,de,para,tipo,circuitos,x_pct,cap_normal_mva,nome,fonte,confianca,nota
2027-04-01,5350,5428,linha,2,1.2,2000,LT 500 kV Açu III – Quixadá,PAR 2026-2030,contratado,

data,nome,fonte_energia,barra,cap_mw,flag_corte,fonte,confianca,nota
2027-08-01,UFV Exemplo,UFV,6140,800,1,ANEEL,previsto,
```

`circuitos` > 1 gera um ramo por circuito. Na API: `GET/POST /api/cenarios`, `POST /api/cenarios/{nome}/eventos`, `DELETE /api/cenarios/{nome}/eventos/{id}`, `POST /api/cenarios/{nome}/importar?tipo=linhas|usinas`, e todas as consultas aceitam `?cenario=`.

## Como rodar

```bash
make setup          # uma vez: .venv e node_modules
make servir         # compila o front e sobe em http://127.0.0.1:8000
```

Do Mac, abra pelo túnel: `ssh -L 8000:127.0.0.1:8000 <maquina-linux>` e acesse `http://localhost:8000`.
`make servir-rede` abre na rede local (`0.0.0.0`). Como não há login, use só em rede confiável.

Para outro cadastro: `make servir CADASTRO=/caminho/do/cadastro`.

Sem `make` ou sem `python3-venv`: `uv venv && uv pip install -e '.[dev]'`, `npm --prefix frontend ci && npm --prefix frontend run build` e `.venv/bin/kurt-app servir --cadastro <pasta>`.
Para desenvolver o front com recarga: `make dev` (Vite em `http://127.0.0.1:5173`).

```bash
make test           # pytest + checagem de tipos do front
make validar        # checagens do cadastro atual
```

## Fixture

`fixtures/acu3_20260910/` é um recorte real, o eixo Açu III do deck DESSEM de 10/09/2026, gerado por `scripts/fixture_acu3.py`. Serve para desenvolver enquanto a extração completa não existe. Limitações:

- `cap_mw` das usinas é aproximada pelo máximo da geração de referência do dia;
- `carga_media_mw` é a média das 48 meias-horas, não a carga do caso base;
- `x_pct` não está no recorte;
- os dois eventos com fonte `demo` (nomes com `[DEMO]`) são **hipotéticos** e só existem para testar a linha do tempo.

## Divisão com o repositório do Kurt

| Repositório | Responsabilidade |
| --- | --- |
| `kurt` | Ler e escrever decks: extração do cadastro a partir dos decks (`kurt config extrair`, a fazer) e gerador de decks a partir do cadastro e das séries (a fazer). |
| `kurt-app` | Ler o cadastro, mostrar, montar cenários e disparar rodadas do Kurt pela linha de comando. Não conhece o formato do deck. |

O contrato entre os dois é o esquema dos Parquet e os subcomandos do Kurt.

## Próximos passos

1. Extração do cadastro do SIN inteiro, jan/25 a nov/26, no repositório do Kurt.
2. Cenário `expansao` povoado com as fontes oficiais (PAR/PEL, cronograma de obras de transmissão, outorgas ANEEL reconciliadas com NEWAVE/DECOMP).
3. Rodadas do Kurt sobre um cenário, com resultados em P10, P50 e P90.

## Estrutura

```
kurt_app/        backend: esquema, cadastro, validação, layout, API, CLI
frontend/        TypeScript + Vite, sem framework; mapa em canvas
fixtures/        cadastros de exemplo
scripts/         geração de fixtures
tests/           pytest
```
