# ADR-0010 — Visibilidade de Consumo de Tokens no ExecutionLog

Status: Aceito
Data: 2026-07-05
Origem: RFC-0001, item R-6
Documento técnico relacionado: `services/agent-os-api/app/db.py`, `dispatcher.py`

---

## Contexto

A comunidade OpenClaw destaca que a diferença entre uma skill bem-otimizada e uma mal escrita pode chegar a 10x em custo de tokens. No Agent OS, mesmo rodando 100% local na Fase 1 (sem custo monetário por token), essa métrica ainda importa — ela se traduz em tempo de GPU ocupado, o recurso mais escasso da RTX 5050.

Hoje, a API do LM Studio já retorna a contagem de tokens de cada chamada (campo `usage` da resposta, no formato compatível com OpenAI), mas o `dispatcher.py` recebe essa informação e a descarta sem registrar. Isso significa que, ao investigar um gargalo futuro (ex: na Fase 2, tentando entender por que um Agent está monopolizando a GPU), o único dado disponível seria `latency_ms` — que não distingue "demorou porque o prompt era gigante" de "demorou porque estava na fila esperando outro Agent".

## Decisão

Capturar `usage.prompt_tokens` e `usage.completion_tokens` da resposta do motor de LLM em `_handle_llm_call()`, propagá-los no `meta` da Envelope de resposta, e persistir como `tokens_input`/`tokens_output` no `ExecutionLog`.

## Alternativas consideradas

1. **Não capturar, considerar prematuro para a Fase 1.** Rejeitado: a informação já chega de graça na resposta existente — o custo de capturá-la é uma leitura de dicionário e duas colunas novas na tabela, muito menor que o valor de diagnóstico que ela habilita mais tarde.
2. **Registrar tokens num sistema de métricas separado (ex: Prometheus) em vez do `ExecutionLog`.** Rejeitado para a Fase 1: introduziria uma peça de infraestrutura nova antes da hora — o `Stack_por_Fase.md` já prevê Prometheus só a partir da Fase 2. Nada impede reexportar esses mesmos dados para Prometheus quando ele existir; a tabela SQLite já registrada não se perde.

## Consequências

**Positivas:**
- Diagnóstico de gargalo de GPU na Fase 2 passa a distinguir "prompt grande" de "fila de espera" sem precisar de instrumentação nova retroativa.
- Mudança de baixo risco: não altera contrato externo, só enriquece um registro interno já existente.

**Negativas / trade-offs aceitos:**
- `tokens_input`/`tokens_output` ficam `null` para chamadas a Tools que não sejam `tool.llm_call` (ex: OCR, transcrição) — aceitável, já que consumo de token só se aplica a chamadas de LLM.
