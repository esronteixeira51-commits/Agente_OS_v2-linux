# =========================================================================
# agent-os-api — Agent OS v2.0
#
# Build multi-stage: separa a instalação de dependências (camada que
# muda pouco, fica em cache) do código da aplicação (muda a cada
# alteração) — mantém os rebuilds rápidos durante desenvolvimento ativo.
# =========================================================================

# ---- Stage 1: build de dependências -------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- Stage 2: imagem final, enxuta ---------------------------------------
FROM python:3.12-slim

# Usuário não-root — segurança básica mesmo num protótipo (Manifesto
# Princípio 10: "segurança sempre vem primeiro").
RUN useradd --create-home --uid 1000 agentos
WORKDIR /app

COPY --from=builder /install /usr/local
COPY ./app ./app

# Diretório onde o SQLite grava — montado como volume no docker-compose.
RUN mkdir -p /data && chown -R agentos:agentos /data /app

USER agentos

EXPOSE 8080

# Healthcheck bate no /health definido em main.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]