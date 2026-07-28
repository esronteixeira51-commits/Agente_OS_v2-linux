"""
Wrapper sobre a API compatível com OpenAI exposta pelo LM Studio (e,
futuramente, vLLM/Ollama/etc.).

Este é o ÚNICO arquivo que sabe qual motor de LLM está rodando — todo
o resto do sistema fala com ele através de tool.llm_call no
dispatcher, nunca chamando call_llm() diretamente fora desta camada
(essa regra é justamente o que estava sendo violada por agent.critic
na v0.1.1 — ver docstring de app.agents).

v2.0: porta 1:1 do v0.1.1 — nenhum bug encontrado aqui na análise.
"""

from __future__ import annotations

from typing import Optional

import httpx

from app.config import settings


class LLMUpstreamError(Exception):
    """Levantado quando o motor de LLM está fora do ar ou retorna erro."""


class LLMTimeoutError(Exception):
    """Levantado quando a chamada excede llm_timeout_seconds."""


async def call_llm(
    messages: list[dict[str, str]],
    model: str = "local-model",
    temperature: float = 0.3,
    tools: Optional[list[dict]] = None,
    tool_choice: str = "auto",
    max_tokens: Optional[int] = None,
) -> dict:
    """
    Chama o endpoint /chat/completions do motor de LLM configurado.

    tools/tool_choice seguem o formato OpenAI de tool calling nativo —
    é isso que permite ao LLM decidir estruturadamente entre responder
    direto ou pedir a execução de uma tool, em vez de parsear texto
    livre tentando adivinhar a intenção.
    """
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{settings.llm_endpoint}/chat/completions",
                headers=headers,
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(str(exc)) from exc
    except httpx.ConnectError as exc:
        raise LLMUpstreamError(f"Não foi possível conectar em {settings.llm_endpoint}: {exc}") from exc

    if response.status_code >= 400:
        raise LLMUpstreamError(f"LLM retornou {response.status_code}: {response.text}")

    return response.json()