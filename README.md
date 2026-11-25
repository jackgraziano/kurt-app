# kurt-app

Aplicação local para montar cenários de simulação do Kurt e projetar curtailment: o sistema físico (rede, usinas, restrições) ao longo do tempo e, aba por aba, os demais insumos de uma rodada.

Roda na máquina Linux, junto com os decks e o binário do Kurt. Não tem login, nuvem nem deploy.

Para quem vai continuar o desenvolvimento (pessoas ou agentes), as diretrizes estão em [AGENTS.md](AGENTS.md).

## Como funciona

```
caso base (Parquet, extraído do deck)  ──►  casos de rede (camadas de eventos datados)
                                                      │
cenário de simulação: horizonte + um insumo por aba ◄─┘  rede · carga · renováveis · hidro ·
                                                          valor da água · térmicas · execução
```

- **Caso base:** o cadastro do sistema físico extraído do deck DESSEM mais recente por `kurt config extrair`. São cinco tabelas em Parquet (`barras`, `ramos`, `usinas`, `restricoes`, `eventos`), cada registro com vigência `[vigente_de, vigente_ate)`. Nunca é alterado.
- **Casos de rede:** camadas de eventos datados sobre o caso base (`entra`, `sai`, `altera`), empilháveis. Ex.: `expansao` (obras e usinas previstas pela ANEEL) e, por cima, uma sensibilidade "LT X atrasa seis meses".
- **Cenários de simulação:** o que uma rodada do Kurt vai usar. Cada um tem nome, horizonte (mês de início e de fim) e, para cada aba, a escolha do insumo. A aba Rede elétrica aponta para um caso de rede.
- **JSON de cada aba:** toda aba de insumo tem um JSON por trás, que pode ser baixado, editado à mão e subido de volta (botões "baixar JSON" e "subir JSON" na barra de abas). O app valida e grava. Hoje isso funciona na Rede elétrica; as próximas abas seguem o mesmo contrato (ver AGENTS.md).

## Telas

- **Tela principal** (`#/`): lista os cenários e cria um novo, com nome, horizonte e caso de rede. O horizonte padrão são os 12 meses seguintes ao caso base.
- **Tela do cenário** (`#/c/<nome>/<aba>`):
  - **Resumo:** descrição, horizonte, caso de rede e o estado de cada insumo (definido, a definir, a fazer).
  - **Rede elétrica:** mapa indicativo sobre o contorno das UFs, com a linha do tempo limitada ao horizonte. No play ou ao arrastar, o que entrou (verde), mudou (roxo) ou saiu (vermelho) no mês pisca no mapa. Barra lateral com os eventos do mês, o editor do caso de rede (linha nova, usina nova, importar CSV, remover evento), a ficha de cada elemento (alterar ou retirar numa data), a validação e informações do cadastro.
  - **Carga, Renováveis, Hidro, Valor da água, Térmicas e Execução:** ainda são esqueleto.

As posições no mapa são indicativas de propósito: cada subestação parte do centro da sua UF e um layout de molas a afasta das vizinhas, sem sair da UF. Não é geografia exata.

## JSON da aba Rede elétrica

```json
{
  "formato": "kurt-app/insumo", "versao": 1, "insumo": "rede", "cenario": "dez26-expansao",
  "horizonte": {"inicio": "2026-12-01", "fim": "2027-11-30"},
  "_ajuda": ["..."],
  "dados": {
    "caso_de_origem": "expansao",
    "caso_base": {"deck": "...", "data_base": "2026-11-20"},
    "eventos": [
      {"data": "2027-04-01", "acao": "entra", "entidade": "ramos", "entidade_id": "R5350-5428-N1",
       "registro": {"de": "B5350", "para": "B5428", "tipo": "linha", "cap_normal_mva": 2000},
       "fonte": "ANEEL SIGET", "confianca": "contratado", "camada": "expansao"},
      {"data": "2027-06-01", "acao": "altera", "entidade": "ramos", "entidade_id": "R5350-5428-1",
       "campos": {"cap_normal_mva": 2400}}
    ]
  }
}
```

`eventos` é a lista completa de mudanças sobre o caso base: todas as camadas do caso de rede, achatadas. Ao subir o arquivo, a lista é validada contra o caso base e gravada como o caso de rede `<cenário>-rede`, que pertence só àquele cenário. O cenário passa a usá-lo, e os casos de rede compartilhados não mudam. Se algum evento não se aplica, por exemplo uma barra inexistente na data ou um ramo que não está vigente, nada é gravado e o erro diz qual evento falhou.

## Casos de rede pela linha de comando e por CSV

```bash
kurt-app cenario criar expansao --cadastro <pasta> --descricao "expansão oficial"
kurt-app cenario importar expansao linhas.csv --cadastro <pasta> --tipo linhas
kurt-app cenario criar sens-lt --cadastro <pasta> --pai expansao
kurt-app cenario listar --cadastro <pasta>
```

O cabeçalho é obrigatório e as barras vão pelo número do deck:

```
data,de,para,tipo,circuitos,x_pct,cap_normal_mva,nome,fonte,confianca,nota
2027-04-01,5350,5428,linha,2,1.2,2000,LT 500 kV Açu III – Quixadá,PAR 2026-2030,contratado,

data,nome,fonte_energia,barra,cap_mw,flag_corte,fonte,confianca,nota
2027-08-01,UFV Exemplo,UFV,6140,800,1,ANEEL,previsto,
```

`circuitos` > 1 gera um ramo por circuito.

### Caso `expansao` a partir das fontes oficiais

- `scripts/expansao_ralie.py`: usinas do RALIE da ANEEL (data prevista por unidade geradora e subestação de conexão), casadas com as barras do deck por nome da subestação, UF e tensão (`kurt_app/expansao.py`). Use `--recriar` para refazer do zero.
- `scripts/expansao_transmissao.py`: LTs, seccionamentos e transformações de 230 kV ou mais do SIGET (`kurt_app/transmissao.py`).

O que não casa com segurança vai para `expansao_pendencias.csv` e `transmissao_pendencias.csv`, ao lado dos casos de rede, com os melhores candidatos, para revisão.

## API

Todas as consultas de rede aceitam `?cenario=<caso de rede>` (padrão `base`).

| Rota | O que faz |
| --- | --- |
| `GET /api/meta` | período, meses com eventos, contagens |
| `GET /api/configuracao?data=&kv_min=&submercado=` | o sistema vigente na data |
| `GET /api/eventos?de=&ate=&entidade=&confianca=` | eventos no período |
| `GET /api/entidade/{tipo}/{id}` | versões e eventos de uma barra, ramo, usina ou restrição |
| `GET /api/diferenca?a=&b=` | o que entrou, saiu ou mudou entre duas datas |
| `GET /api/validacao` | checagens de qualidade |
| `GET/POST /api/cenarios`, `GET/DELETE /api/cenarios/{nome}` | casos de rede |
| `POST /api/cenarios/{nome}/eventos`, `DELETE .../eventos/{id}`, `POST .../importar?tipo=` | eventos de um caso de rede |
| `GET /api/insumos` | as abas de insumo |
| `GET/POST /api/simulacoes`, `GET/PATCH/DELETE /api/simulacoes/{nome}` | cenários de simulação |
| `GET /api/simulacoes/{nome}/insumos/{aba}[?baixar=1]` | JSON da aba |
| `POST /api/simulacoes/{nome}/insumos/{aba}` | sobe o JSON da aba |
| `GET /api/decks/fontes` | fontes de decks DECOMP configuradas |
| `GET /api/decks?tipo&caminho&inicio&fim` | decks de uma fonte e o mapa semana → deck |
| `GET /api/simulacoes/{nome}/semana?inicio&cenario` | séries semi-horárias de uma semana (gráficos de Carga e Renováveis) |
| `GET /api/simulacoes/{nome}/energia?inicio&cenario` | MW médio por dia de carga, MMGD, EOL e UFV por submercado, antes das sensibilidades (tabela de GWmed) |
| `GET/POST /api/simulacoes/{nome}/rodadas`, `GET .../rodadas/{id}`, `POST .../rodadas/{id}/cancelar`, `GET .../rodadas/{id}/resultados` | rodadas do Kurt e resultados P10/P50/P90 |
| `GET /api/ufs`, `GET /api/esquema` | contorno das UFs e esquema das tabelas |

## Como rodar

```bash
make setup          # uma vez: .venv e node_modules
make servir         # compila o front e sobe em http://127.0.0.1:8000
make servir CADASTRO=dados/cadastro/base-20261120   # com o caso base do SIN
```

- **Do Mac:** abra pelo túnel `ssh -L 8000:127.0.0.1:8000 <maquina-linux>` e acesse `http://localhost:8000`. Também dá para usar `make servir-rede`, que abre na rede local (`0.0.0.0`); como não há login, use só em rede confiável.
- **Sem `make` ou sem `python3-venv`:**
  - `uv venv && uv pip install -e '.[dev]'`;
  - `npm --prefix frontend ci && npm --prefix frontend run build`;
  - `.venv/bin/kurt-app servir --cadastro <pasta>`.

```bash
make dev            # backend com recarga + Vite em http://127.0.0.1:5173
make test           # ruff (lint e formato), pytest e checagem de tipos do front
make validar        # checagens do cadastro
```

Dados locais, fora do git (`dados/`):

| Pasta | Conteúdo | Variável |
| --- | --- | --- |
| `dados/cadastro/<nome>/` | casos base extraídos | `KURT_APP_CADASTRO` |
| `dados/cenarios/<cadastro>/` | casos de rede, um JSON por caso | `KURT_APP_CENARIOS` |
| `dados/simulacoes/<cadastro>/` | cenários de simulação, um JSON por cenário | `KURT_APP_SIMULACOES` |
| pastas de decks DECOMP (uma subpasta ou .zip por deck) | fontes da aba Decks | `KURT_APP_DECKS` (separadas por `:`) |
| decks oficiais do ONS (`<ano>/<AAAAMMDD>__DEC_ONS_..._VE.zip`) | fonte da aba Decks, para backtest | `KURT_APP_DECKS_ONS` |
| executável que roda um dia no Kurt (`scripts/rodar_dia.sh` do Kurt) | disparo de rodadas | `KURT_APP_RODAR_DIA` |
| arquivo com `usuario:senha`; liga a senha (HTTP Basic) em todo o app, para expor por túnel | servidor | `KURT_APP_SENHA_ARQUIVO` |

## Fixture

`fixtures/acu3_20260910/` é um recorte real: o eixo Açu III do deck DESSEM de 10/09/2026, gerado por `scripts/fixture_acu3.py`. É usado nos testes e para desenvolver sem o caso base do SIN. Tem algumas limitações:

- `cap_mw` é o máximo da geração de referência do dia;
- `x_pct` não está no recorte;
- os eventos com fonte `demo` (nomes com `[DEMO]`) são hipotéticos.

## Divisão com o repositório do Kurt

| Repositório | Responsabilidade |
| --- | --- |
| `kurt` | Ler e escrever decks: `kurt config extrair <deck.zip> <saida>` gera o caso base; o gerador de decks a partir de um cenário ainda está por fazer. Roda o PL. |
| `kurt-app` | Ler o cadastro, montar casos de rede e cenários de simulação, mostrar e, depois, disparar rodadas do Kurt pela linha de comando. Não conhece o formato do deck. |

O contrato entre os dois é o esquema dos Parquet (`kurt_app/esquema.py`) e os subcomandos do Kurt.

## Estrutura

```
kurt_app/
  esquema.py        contrato das tabelas do cadastro
  cadastro.py       consultas por data (DuckDB)
  cenario.py        casos de rede: eventos datados sobre o caso base
  simulacao.py      cenários de simulação e a lista de abas (INSUMOS)
  insumo_json.py    JSON de cada aba: exportar e importar
  validar.py        checagens do cadastro
  layout.py         posições indicativas (molas, dentro da UF); ufs.json
  expansao.py, transmissao.py   expansão a partir do RALIE e do SIGET
  importar.py       CSV de linhas e usinas
  api.py, cli.py
frontend/src/
  main.ts           casca: rotas, tela principal, tela do cenário, abas
  rede.ts           aba Rede elétrica (mapa, linha do tempo, editor)
  mapa.ts           desenho em canvas
  api.ts, tipos.ts, util.ts, estilo.css
scripts/            fixture, malha das UFs, montagem do caso expansao
tests/              pytest, só com a fixture e sem rede
```

## Próximos passos

1. Lógica das abas de insumo, cada uma com o seu JSON: Carga, Renováveis, Hidro, Valor da água, Térmicas e Execução.
2. Revisar as pendências do caso `expansao`, como usinas sem ponto de conexão e casamentos ambíguos, e reconciliar os totais com NEWAVE/DECOMP.
3. Gerador de deck a partir de um cenário (no Kurt) e rodadas pela aba Execução, com resultados em P10, P50 e P90.
