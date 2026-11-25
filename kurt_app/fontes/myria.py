"""Cliente mínimo e só de leitura da API v1 do Myria (biblioteca padrão; nada de escrita).

Configuração:
- ``KURT_APP_MYRIA_API``: endereço do ambiente (padrão ``https://myria.energy``);
- ``KURT_APP_MYRIA_CHAVE``: caminho do arquivo com a chave (padrão
  ``~/.config/myria/credentials/teko-prod-full-access-2026-11-24.key``).

A chave é lida do arquivo só na hora de montar o cabeçalho e nunca é impressa, registrada em
log nem incluída em mensagens de erro. Só são feitas requisições GET.

Rotas usadas (``/api/v1``):
- ``GET /studies`` e ``GET /studies/{study_key}`` (estudo com ``rodadas[]``);
- ``GET /studies/{study_key}/rodadas/{round_key}/files`` e ``.../files/{filename}``;
- ``GET /studies/{study_key}/executions``, ``GET /executions/{id}/artifacts`` e
  ``GET /executions/{id}/artifacts/{artifact_id}/download``.
As listagens devolvem ``{"data": [...], "page": {"has_more", "next_cursor"}}``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

API_PADRAO = "https://myria.energy"
CHAVE_PADRAO = Path.home() / ".config" / "myria" / "credentials" / "teko-prod-full-access-2026-11-24.key"

Transporte = Callable[[str, dict[str, str]], tuple[int, bytes]]


class MyriaErro(RuntimeError):
    pass


def _transporte_urllib(url: str, cabecalhos: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=cabecalhos, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Myria:
    def __init__(
        self,
        api: str | None = None,
        arquivo_chave: str | Path | None = None,
        transporte: Transporte | None = None,
    ):
        self.api = (api or os.environ.get("KURT_APP_MYRIA_API") or API_PADRAO).rstrip("/")
        self._arquivo_chave = Path(arquivo_chave or os.environ.get("KURT_APP_MYRIA_CHAVE") or CHAVE_PADRAO)
        self._transporte = transporte or _transporte_urllib

    # -- baixo nível ---------------------------------------------------------
    def _cabecalhos(self) -> dict[str, str]:
        try:
            chave = self._arquivo_chave.read_text().strip()
        except OSError:
            raise MyriaErro(f"não consegui ler a chave em {self._arquivo_chave}") from None
        if not chave:
            raise MyriaErro(f"arquivo de chave vazio: {self._arquivo_chave}")
        return {"Authorization": f"Bearer {chave}", "Accept": "application/json", "User-Agent": "kurt-app"}

    def _url(self, caminho: str, params: dict[str, Any] | None = None) -> str:
        url = (
            caminho
            if caminho.startswith("http")
            else f"{self.api}/api/v1{caminho if caminho.startswith('/') else '/' + caminho}"
        )
        if caminho.startswith("/api/"):
            url = f"{self.api}{caminho}"
        q = {k: v for k, v in (params or {}).items() if v is not None}
        return f"{url}?{urllib.parse.urlencode(q)}" if q else url

    def _get(self, caminho: str, params: dict[str, Any] | None = None) -> bytes:
        url = self._url(caminho, params)
        status, corpo = self._transporte(url, self._cabecalhos())
        if status != 200:
            detalhe = ""
            try:
                j = json.loads(corpo)
                detalhe = j.get("error", {}).get("code") or j.get("code") or j.get("detail") or ""
            except (ValueError, AttributeError):
                pass
            raise MyriaErro(f"GET {url.split('?')[0]} respondeu {status} {detalhe}".strip())
        return corpo

    def get_json(self, caminho: str, params: dict[str, Any] | None = None) -> Any:
        return json.loads(self._get(caminho, params))

    def paginar(self, caminho: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        p = dict(params or {})
        p.setdefault("limit", 100)
        while True:
            r = self.get_json(caminho, p)
            itens = r.get("data") if isinstance(r, dict) else r
            yield from itens or []
            page = r.get("page", {}) if isinstance(r, dict) else {}
            if not page.get("has_more") or not page.get("next_cursor"):
                return
            p["cursor"] = page["next_cursor"]

    # -- estudos e rodadas ---------------------------------------------------
    def listar_estudos(self) -> list[dict]:
        return list(self.paginar("/studies"))

    def estudo(self, study_key: str) -> dict:
        return self.get_json(f"/studies/{study_key}")

    def arquivos(self, study_key: str, round_key: str, tipo: str = "all") -> list[dict]:
        """Arquivos da raiz da rodada (``kind``: all, input, output ou unknown)."""
        return list(self.paginar(f"/studies/{study_key}/rodadas/{round_key}/files", {"kind": tipo}))

    def baixar_arquivo(
        self, study_key: str, round_key: str, nome: str, pasta: str | Path, sobrescrever: bool = False
    ) -> Path | None:
        destino = Path(pasta) / nome
        if destino.exists() and not sobrescrever:
            return None
        corpo = self._get(f"/studies/{study_key}/rodadas/{round_key}/files/{urllib.parse.quote(nome)}")
        return _gravar(destino, corpo)

    # -- execuções e artefatos -----------------------------------------------
    def execucoes(self, study_key: str) -> list[dict]:
        return list(self.paginar(f"/studies/{study_key}/executions"))

    def artefatos(self, execucao: str) -> list[dict]:
        return list(self.paginar(f"/executions/{execucao}/artifacts"))

    def baixar(self, artefato: dict, pasta: str | Path, sobrescrever: bool = False) -> Path | None:
        """Baixa um artefato (dicionário de ``artefatos()``) com o nome lógico, em `pasta`."""
        nome = Path(artefato["logical_name"]).name
        destino = Path(pasta) / nome
        if destino.exists() and not sobrescrever:
            return None
        url = (
            artefato.get("download_url")
            or f"/executions/{artefato['execution_id']}/artifacts/{artefato['artifact_id']}/download"
        )
        return _gravar(destino, self._get(url))


def _gravar(destino: Path, corpo: bytes) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".parcial")
    tmp.write_bytes(corpo)
    tmp.replace(destino)
    return destino
