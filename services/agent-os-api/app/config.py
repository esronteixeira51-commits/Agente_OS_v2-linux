"""
Configurações centrais do agent-os-api.

Todas as configurações vêm de variáveis de ambiente (definidas no .env
e injetadas pelo docker-compose.yml) — nunca hardcoded. Isso é o que
permite trocar de motor de LLM (LM Studio -> vLLM, por exemplo) só
mudando LLM_ENDPOINT, sem tocar em nenhuma linha de código.

v2.0: porta 1:1 do v0.1.1 — nenhum bug encontrado aqui na análise.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Endpoint do motor de LLM. Ver 09-INFRASTRUCTURE/Preparacao_Ambiente_Linux.md
    # para como isso é configurado no host Linux (host.docker.internal).
    llm_endpoint: str = "http://host.docker.internal:1234/v1"
    llm_api_key: str = ""

    vector_db_host: str = "vector-db"
    vector_db_port: int = 8000

    relational_db_path: str = "/data/agent_os.db"
    search_endpoint: str = "http://search:8080"

    ocr_endpoint: str = "http://ocr-worker:8090"
    transcription_endpoint: str = "http://transcription-worker:8095"

    # Identificador do modelo tal como o LM Studio o expõe em
    # /v1/models — precisa bater exatamente (LM Studio silenciosamente
    # troca para outro modelo carregado se o nome não for reconhecido,
    # sem avisar; ver ADR-0013 para o caso real que motivou isso virar
    # configuração em vez de hardcode dentro de app.agents).
    default_model: str = "qwen/qwen3-8b"

    log_level: str = "INFO"

    # Calibrado para RTX 5050 8GB — geração mais lenta quando o modelo
    # exige offload parcial para CPU em respostas longas.
    llm_timeout_seconds: float = 180.0
    ocr_timeout_seconds: float = 120.0
    transcription_timeout_seconds: float = 300.0

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()