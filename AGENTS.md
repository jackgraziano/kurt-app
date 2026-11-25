# AGENTS.md: diretrizes para continuar o kurt-app

Leia também o [README.md](README.md), que descreve o que o app faz, a API e como rodar. Este arquivo diz **como trabalhar** no projeto.

## Objetivo

Plataforma local para projetar curtailment com o Kurt, um modelo físico em Rust que reproduz o PL do DESSEM. O usuário monta um **cenário de simulação** (horizonte e insumos, aba por aba), vê o sistema evoluir no tempo e, mais adiante, dispara rodadas e vê os resultados em P10, P50 e P90 por mês, submercado, tipo de corte (ENE e rede) e usina.

## Conceitos (use estes nomes)

| Termo | O que é | Onde |
| --- | --- | --- |
| caso base | cadastro extraído do deck mais recente; imutável | Parquet em `dados/cadastro/<nome>/` |
| caso de rede | camada de eventos datados sobre o caso base, com pai opcional | `kurt_app/cenario.py`, `dados/cenarios/` |
| cenário (de simulação) | horizonte + um insumo por aba | `kurt_app/simulacao.py`, `dados/simulacoes/` |
| aba / insumo | decks, rede, carga, renovaveis, hidro, valor_agua, termicas, execucao | `INSUMOS` em `simulacao.py` |
| semana operativa | sábado a sexta; pertence ao mês da sexta-feira | `kurt_app/decks.py` |
| evento | `entra`, `sai` ou `altera`, com data, fonte e confiança | `esquema.py` (tabela `eventos`) |

Atenção: no código e na API os casos de rede ainda se chamam `cenarios` (`/api/cenarios`, `Cenarios`, `cenario.py`). Na interface, "cenário" é sempre o cenário de simulação. Não misture os dois em textos novos.

## Diretrizes de produto

1. **Toda aba de insumo tem um JSON por trás, que o usuário baixa e sobe de volta.** É a porta para qualquer ajuste manual que a interface ainda não oferece. O contrato está em `kurt_app/insumo_json.py`:
   - envelope `{"formato": "kurt-app/insumo", "versao": 1, "insumo": <aba>, "cenario", "horizonte", "_ajuda": [...], "dados": {...}}`;
   - `dados` contém **tudo** o que a aba decide, num formato legível e editável à mão (nomes em português, datas AAAA-MM-DD, ids que aparecem no app);
   - `_ajuda` explica, em frases curtas, como editar; é ignorado na subida;
   - a subida valida tudo antes de gravar e, se algo falhar, não grava nada e diz qual item falhou;
   - baixar e subir sem editar não muda o resultado (ida e volta estável);
   - se o formato de `dados` mudar de forma incompatível, suba `versao` e aceite a anterior enquanto der.
2. **O caso base nunca é editado.** Toda mudança no sistema físico é um evento datado numa camada. A subida do JSON da rede cria um caso próprio do cenário (`<cenário>-rede`, com `"simulacao": <cenário>` no arquivo) e não altera casos compartilhados.
3. **Validar na escrita.** Eventos são aplicados sobre o caso base antes de gravar (`cenario.aplicar`). Um caso de rede usado por algum cenário não pode ser apagado. Apagar um cenário apaga os casos de rede que eram só dele.
4. **Rastreabilidade:** todo evento tem `fonte` (ANEEL RALIE, SIGET, usuario...) e `confianca` (realizado, contratado, previsto, especulativo). O mapa mostra tracejado o que não é realizado.
5. **Mapa indicativo:** as posições são aproximadas, dentro da UF. Não gaste tempo com coordenadas exatas.
6. **Local e sem login:** sem nuvem, sem deploy, sem autenticação. Serve na máquina Linux, na porta 8000.

## Como acrescentar ou dar lógica a uma aba de insumo

1. Em `kurt_app/simulacao.py`, a aba já existe em `INSUMOS`. Ao implementá-la, mude `disponivel` para `True` e descreva o escopo em `descricao`.
2. Em `kurt_app/insumo_json.py`, registre em `ABAS[<aba>]` um par `(exportar, importar)`:
   - `exportar(sims, cenario) -> (dados, ajuda)`;
   - `importar(sims, cenario, dados) -> cenario atualizado`, validando tudo e levantando `SimulacaoErro` com uma mensagem útil.
   Sem registro, vale o genérico, que guarda `dados` como está em `insumos[<aba>]`.
3. Dados volumosos (séries, cortes da FCF) não vão dentro do JSON do cenário. Guarde-os em arquivos próprios em `dados/` e deixe no insumo só a referência e as escolhas. O JSON baixado pode trazer esses dados quando forem editáveis à mão em tamanho razoável; se não forem, traga a referência e os parâmetros.
4. No front, a tela da aba entra em `frontend/src/main.ts`, em `telaCenario`. Para abas grandes, crie um módulo próprio como `rede.ts`, carregado com `import()`. Os botões "baixar JSON" e "subir JSON" aparecem sozinhos quando `disponivel` é verdadeiro.
5. Escreva testes em `tests/`: ida e volta do JSON, subida inválida que não grava, e o efeito no que o Kurt vai receber.

Escopo combinado de cada aba:

| Aba | Escopo |
| --- | --- |
| Decks | decks DECOMP do cenário (pasta, como um estudo do Myria baixado, ou os oficiais do ONS) e o deck e estágio de cada semana operativa; mês sem deck repete o anterior. As outras abas tiram daqui os níveis |
| Rede elétrica | topologia e usinas com expansão datada, mais as restrições elétricas (RESP, LPP, RE, limites de intercâmbio) |
| Carga | demanda por submercado e por barra, mensal e na curva horária |
| Renováveis | eólica e solar (séries e cenários), usinas não despacháveis (flag 0), MMGD e limites de corte |
| Hidro | afluências, níveis iniciais, FPHA e restrições hidráulicas (operuh, defluências) |
| Valor da água | FCF (cortes do DECOMP) por semana do horizonte: rodadas de planejamento, DECOMP oficial ou repetir a do deck base. Sem FCF o Kurt não roda. |
| Térmicas | disponibilidade, inflexibilidade e CVU; manutenções são ignoradas por decisão do usuário |
| Execução | dias (todos ou típicos), discretização, número de cenários, saídas e opções do PL; também é daqui que a rodada será disparada |

## Diretrizes de código

- **Python 3.11+**, com FastAPI, DuckDB, pyarrow e numpy. Não acrescente dependência sem necessidade clara.
- **Front em TypeScript + Vite, sem framework.** Use DOM direto, mapa em canvas e rotas por hash (`#/`, `#/c/<nome>/<aba>`). Escape tudo o que vem de dados com `esc()`.
- **Português** em nomes de domínio, mensagens, comentários e textos da interface. Mensagens de erro dizem o que fazer.
- **Erros do domínio** (`CenarioErro`, `SimulacaoErro`, `KeyError`) são traduzidos em HTTP 422 ou 404 num único lugar, `_erros()` em `api.py`. Não espalhe `try/except` nos endpoints.
- **Gravação de JSON é atômica:** grave em um arquivo temporário e troque com `replace()`.
- **Testes** usam só a fixture `fixtures/acu3_20260910` e pastas temporárias. Nada de rede nem de dados de `dados/`.
- **Antes de commitar:** rode `make test`, que executa ruff check, ruff format --check, pytest e tsc, e `npm --prefix frontend run build`. Na máquina Linux não há `make`: rode `.venv/bin/ruff check kurt_app scripts tests`, `.venv/bin/ruff format --check kurt_app scripts tests`, `.venv/bin/pytest -q` e `npm --prefix frontend run check`.
- **Teste de verdade no navegador:** no ambiente remoto de agentes, o Playwright e o Chromium já vêm instalados. Na máquina Linux, só há o Google Chrome em `/usr/bin/google-chrome`: instale o `playwright-core` numa pasta temporária e use `executablePath`. Suba o servidor numa porta livre, com `KURT_APP_CADASTRO`, `KURT_APP_CENARIOS` e `KURT_APP_SIMULACOES` apontando para pastas temporárias, e confira que o console fica sem erros.

## Datalake do setor elétrico

Há um datalake do setor elétrico em `/mnt/nas`, na máquina Linux. Todo agente pode e deve consultá-lo para conhecer os dados antes de desenhar ou implementar qualquer insumo: decks oficiais (NEWAVE, DECOMP, DESSEM), históricos do ONS, vazões e dados da ANEEL. Prefira o que está lá a baixar da internet. Um levantamento do conteúdo fica em `dados/levantamento/`; atualize-o quando achar algo novo. Use só leitura, rode com `nice` e não copie volumes grandes para fora do NAS.

## Ambiente e operação

- **Máquina Linux** (usuário Jackson): repositórios em `~/code/kurt` e `~/code/kurt-app`. O caso base do SIN está em `~/code/kurt-app/dados/cadastro/base-20261120`. Os decks ficam no NAS, em `/mnt/nas/personal_folder/Decks`.
- **Servidor:** `kurt-app servir --cadastro <caso base> --host 0.0.0.0 --porta 8000`, iniciado com `setsid`, com log em `~/code/kurt-app/servir.log`. Depois de um pull, recompile o front e reinicie.
- **Atualizar o caso base:**
  1. No Kurt, rode `kurt config extrair <deck.zip> <saida>`, que grava JSONL.
  2. Converta para Parquet com `scripts/cadastro_parquet.py`, do repositório do Kurt, usando o Python do kurt-app (`~/code/kurt-app/.venv/bin/python`), porque ele usa `kurt_app.escrita`.
  3. Rode `kurt-app layout <pasta>`, que leva cerca de 100 s para o SIN.
  4. Rode `kurt-app validar <pasta>`.
  Os decks DESSEM ficam no NAS, em `data/raw/ons/dessem/entrada-saida`. Os ids de barras, ramos e usinas precisam ser estáveis entre extrações, porque os eventos os referenciam.
- **Kurt:** a máquina não tem Rust instalado; compile sempre com `scripts/cargo.sh`, do repositório do Kurt, que usa Docker. Não compile em `~/code/kurt` enquanto houver lotes rodando, porque isso troca o binário em uso. Nessas horas, use um worktree com `CARGO_TARGET_DIR` próprio e `nice`.
- **Git:** trabalhe na `main` com commits pequenos e mensagens em português, ou numa branch quando outra frente estiver mexendo no mesmo código. Apague as branches e os worktrees depois do merge. Só uma frente por vez edita o kurt-app.
- **Sem dados pessoais** em requisições a serviços externos.

## Estado atual e pendências conhecidas

- **Caso base:** SIN, deck de 20/11/2026, com 12.953 barras, 17.019 ramos, 4.147 usinas e 314 restrições. Aproximações:
  - `cap_mw` de EOL e UFV é o máximo da previsão do dia, porque o deck traz 9999;
  - barras de terminal de gerador (990 a 992 kV) foram agrupadas na subestação de alta.
- **Caso `expansao`:** 817 eventos, sendo 589 de usinas do RALIE e 228 de obras do SIGET. Só cerca de 40% da potência do RALIE casou com o deck; o resto está nos CSVs de pendências. As datas de 2031 são em boa parte datas-limite de outorga, não previsões.
- **Abas:** todas têm lógica. Carga, Renováveis, Térmicas, Hidro, Valor da água e Execução tiram o nível dos decks da aba Decks (`niveis.py`) e a forma do histórico (`fontes/formas.py`, Parquet em `dados/formas/`, gerados por `scripts/formas_historico.py`; outro lugar via `KURT_APP_FORMAS`). Cada uma guarda só `opcoes` e `sobrescritos` (`abas.py`); a calibração está em `series.py`. O desenho está no doc "kurt-app: projeção a partir de decks DECOMP".
- **Pasta do dia:** `kurt-app dia <cenário> <AAAA-MM-DD> <saida> [--cenario k]` (`dia.py`) grava tudo o que o Kurt recebe para um dia: séries de carga, MMGD, ANDE e renováveis por usina, térmicas, vazões, volumes, FCF e a rede vigente. É o contrato com o gerador de deck do Kurt.
- **Sensibilidades das renováveis:** na aba Renováveis, `opcoes.sensibilidade` tem `nivel` (fator por fonte EOL/UFV/MMGD × submercado, opcionalmente por mês) e `forma` (24 pesos horários que mudam a hora sem mudar a energia do dia), aplicados depois da calibração no DECOMP (`series.multiplicador`).
- **Myria:** `fontes/myria.py` só lê (GET). A chave fica num arquivo na máquina Linux (caminho em `KURT_APP_MYRIA_CHAVE`); nunca a imprima, copie nem grave.
- **Fontes de decks** são configuradas por ambiente: `KURT_APP_DECKS` (pastas, separadas por `:`) e `KURT_APP_DECKS_ONS` (raiz dos decks oficiais, `.../ons/decomp/valor-esperado`).
- **Rodadas** (`rodada.py`): a aba Execução dispara o plano (dias × cenários) em segundo plano. Cada tarefa grava a pasta do dia e chama `KURT_APP_RODAR_DIA` (`scripts/rodar_dia.sh` do Kurt) com o contrato `<pasta_do_dia> <saida>` → `corte.csv`, `resumo.json` e `log.txt`. Os resultados são P10, P50 e P90 entre cenários, em GWh, por mês, submercado, tipo (ENE e rede) e usina.
- **Ainda não existe:** o `rodar_dia.sh` de verdade (gerador de deck do dia, branch feat/deck-dia do Kurt).
