"""
Configurações centrais do agent-os-api.

Todas as configurações vêm de variáveis de ambiente (definidas no .env
e injetadas pelo docker-compose.yml) — nunca hardcoded. Isso é o que
permite trocar LM Studio -> vLLM na Fase 2 só mudando LLM_ENDPOINT,
sem tocar em nenhuma linha de código.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Endpoint do motor de LLM. Fase 1: LM Studio no host.
    # Fase 2: vLLM ou LiteLLM em outro serviço do compose.
    llm_endpoint: str = "http://host.docker.internal:1234/v1"
    llm_api_key: str = ""

    # Papel "vector-db" — ver ADR-0003. Nome do serviço nunca muda,
    # só a imagem por trás dele no docker-compose.yml. Host/port
    # discretos (em vez de uma URL única) porque o cliente Python do
    # ChromaDB (chromadb.HttpClient) exige os dois separados.
    vector_db_host: str = "vector-db"
    vector_db_port: int = 8000

    relational_db_path: str = "/data/agent_os.db"
    search_endpoint: str = "http://search:8080"

    # Papel "ocr-worker" — serviço especialista de extração de texto.
    # Roda em CPU de propósito, ver Stack_por_Fase.md seção 3.6.
    ocr_endpoint: str = "http://ocr-worker:8090"

    # Papel "transcription-worker" — serviço especialista de
    # transcrição de áudio. Também CPU de propósito na Fase 1.
    transcription_endpoint: str = "http://transcription-worker:8095"

    log_level: str = "INFO"

    # Timeout default para chamadas ao motor de LLM, em segundos.
    # Aumentado de 60s -> 180s depois de um timeout real observado em
    # respostas longas (ex: matemática com "mostre o cálculo") na
    # RTX 5050 8GB — geração mais lenta quando o modelo exige offload
    # parcial para CPU. Vira mais generoso ainda, ou desnecessário
    # neste patamar, na Fase 2/3 com GPUs maiores.
    llm_timeout_seconds: float = 180.0

    # OCR de PDF grande pode demorar bem mais que uma chamada de LLM
    # curta — timeout separado e mais generoso.
    ocr_timeout_seconds: float = 120.0

    # Transcrição de áudio em CPU é a operação mais lenta do stack da
    # Fase 1 — um áudio de 10 minutos pode levar vários minutos para
    # processar num Ryzen 7 5700G. Timeout generoso de propósito.
    transcription_timeout_seconds: float = 300.0

    class Config:
        env_file = ".env"


settings = Settings()
