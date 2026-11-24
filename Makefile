# kurt-app: comandos de uso local (máquina Linux).
SHELL   := /bin/bash
PY      ?= python3
VENV    := .venv
BIN     := $(VENV)/bin
CADASTRO ?= fixtures/acu3_20260910
PORTA   ?= 8000

.PHONY: setup dev servir servir-rede front test validar fixture limpar

setup:            ## cria o ambiente Python e instala o front
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -e '.[dev]'
	cd frontend && npm install

dev:              ## backend com recarga + Vite em http://127.0.0.1:5173
	trap 'kill 0' EXIT; \
	KURT_APP_CADASTRO=$(CADASTRO) $(BIN)/uvicorn --factory kurt_app.api:criar_app --reload --port $(PORTA) & \
	cd frontend && npm run dev

front:            ## compila o front para frontend/dist
	cd frontend && npm run build

servir: front     ## sobe tudo em http://127.0.0.1:$(PORTA) (abrir pelo túnel SSH)
	$(BIN)/kurt-app servir --cadastro $(CADASTRO) --porta $(PORTA)

servir-rede: front ## sobe aberto na rede local (sem login: só em rede confiável)
	$(BIN)/kurt-app servir --cadastro $(CADASTRO) --porta $(PORTA) --host 0.0.0.0

test:             ## testes do backend e checagem de tipos do front
	$(BIN)/pytest -q
	cd frontend && npm run check

validar:          ## checagens de qualidade do cadastro
	$(BIN)/kurt-app validar $(CADASTRO)

fixture:          ## regenera a fixture de Açu III (JSON=caminho/acu3_20260910_dados.json)
	$(BIN)/python scripts/fixture_acu3.py $(JSON)

limpar:
	rm -rf frontend/dist .pytest_cache
