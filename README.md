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

## Como rodar

```bash
make setup          # uma vez: .venv e node_modules
make servir         # compila o front e sobe em http://127.0.0.1:8000
```

Do Mac, abra pelo túnel: `ssh -L 8000:127.0.0.1:8000 <maquina-linux>` e acesse `http://localhost:8000`.
`make servir-rede` abre na rede local (`0.0.0.0`). Como não há login, use só em rede confiável.

Para outro cadastro: `make servir CADASTRO=/caminho/do/cadastro`.
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
2. Expansão futura como eventos previstos (NEWAVE/DECOMP, ANEEL, PAR/PEL).
3. Cenários (cadastro + eventos do usuário) e rodadas do Kurt, com resultados em P10, P50 e P90.

## Estrutura

```
kurt_app/        backend: esquema, cadastro, validação, layout, API, CLI
frontend/        TypeScript + Vite, sem framework; mapa em canvas
fixtures/        cadastros de exemplo
scripts/         geração de fixtures
tests/           pytest
```
