"""
Testes de app.llm_client — usa httpx.MockTransport para simular o
motor de LLM sem precisar de LM Studio real rodando. Cobre os três
caminhos de erro que o dispatcher depende para mapear TOOL_TIMEOUT /
UPSTREAM_UNAVAILABLE corretamente.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm_client import LLMTimeoutError, LLMUpstreamError, call_llm


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler):
    """Substitui httpx.AsyncClient (usado dentro de call_llm) por um
    cliente que roda contra um MockTransport, sem rede real."""

    def factory(*args, **kwargs):
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.llm_client.httpx.AsyncClient", factory)


@pytest.mark.asyncio
class TestCallLlmSuccess:
    async def test_returns_parsed_json_response(self, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "oi"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            )

        _patch_client(monkeypatch, handler)

        result = await call_llm(messages=[{"role": "user", "content": "oi"}])
        assert result["choices"][0]["message"]["content"] == "oi"

    async def test_sends_messages_and_temperature_in_body(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _patch_client(monkeypatch, handler)

        await call_llm(messages=[{"role": "user", "content": "x"}], temperature=0.7)
        assert captured["body"]["messages"] == [{"role": "user", "content": "x"}]
        assert captured["body"]["temperature"] == 0.7

    async def test_tools_and_tool_choice_included_when_provided(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _patch_client(monkeypatch, handler)

        fake_tool = {"type": "function", "function": {"name": "calculator"}}
        await call_llm(messages=[{"role": "user", "content": "x"}], tools=[fake_tool])
        assert captured["body"]["tools"] == [fake_tool]
        assert captured["body"]["tool_choice"] == "auto"

    async def test_tools_omitted_from_body_when_not_provided(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _patch_client(monkeypatch, handler)

        await call_llm(messages=[{"role": "user", "content": "x"}])
        assert "tools" not in captured["body"]

    async def test_authorization_header_added_when_api_key_set(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["headers"] = request.headers
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _patch_client(monkeypatch, handler)
        monkeypatch.setattr("app.llm_client.settings.llm_api_key", "minha-chave")

        await call_llm(messages=[{"role": "user", "content": "x"}])
        assert captured["headers"]["authorization"] == "Bearer minha-chave"

    async def test_no_authorization_header_when_api_key_empty(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["headers"] = request.headers
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _patch_client(monkeypatch, handler)
        monkeypatch.setattr("app.llm_client.settings.llm_api_key", "")

        await call_llm(messages=[{"role": "user", "content": "x"}])
        assert "authorization" not in captured["headers"]


@pytest.mark.asyncio
class TestCallLlmErrors:
    async def test_5xx_response_raises_llm_upstream_error(self, monkeypatch):
        def handler(request):
            return httpx.Response(500, text="Internal Server Error")

        _patch_client(monkeypatch, handler)

        with pytest.raises(LLMUpstreamError):
            await call_llm(messages=[{"role": "user", "content": "x"}])

    async def test_4xx_response_raises_llm_upstream_error(self, monkeypatch):
        def handler(request):
            return httpx.Response(400, text="Bad Request")

        _patch_client(monkeypatch, handler)

        with pytest.raises(LLMUpstreamError):
            await call_llm(messages=[{"role": "user", "content": "x"}])

    async def test_connect_error_raises_llm_upstream_error(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("recusado", request=request)

        _patch_client(monkeypatch, handler)

        with pytest.raises(LLMUpstreamError):
            await call_llm(messages=[{"role": "user", "content": "x"}])

    async def test_timeout_raises_llm_timeout_error_not_upstream_error(self, monkeypatch):
        def handler(request):
            raise httpx.TimeoutException("excedeu o tempo", request=request)

        _patch_client(monkeypatch, handler)

        with pytest.raises(LLMTimeoutError):
            await call_llm(messages=[{"role": "user", "content": "x"}])