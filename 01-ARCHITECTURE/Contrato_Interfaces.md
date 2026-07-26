# Contrato de Interfaces entre Camadas — Agent OS

Versão: 2.0
Status: Implementado — sincronizado com `services/agent-os-api/app/schemas.py`
Camada: 02-CORE / 14-TECHNICAL-SPECS
Depende de: Manifesto do Agent OS (Princípios 1, 2, 6, 7, 10), ADR-0001, ADR-0008, ADR-0013

> **Nota de sincronização (v2.0):** ao reescrever este documento junto da
> implementação nova, foram encontrados dois pontos onde a v0.1.1 já tinha
> divergido entre código e documentação: o tipo `pending_confirmation` da
> Envelope não estava descrito aqui (seção 2), e a tabela de `error_code`
> (seção 6) listava só 5 dos 9 códigos realmente implementados no enum
> `ErrorCode`. Ambos corrigidos abaixo. Esse é exatamente o tipo de desvio
> que `tests/test_schemas.py` agora existe para pegar cedo — ver seção 11.

---

# 1. Objetivo

Este documento define o **contrato universal de mensagens** entre as camadas do Agent OS:

```
Runtime → Agent → Skill → Tool
```

e o caminho de volta:

```
Tool → Skill → Agent → Runtime
```

O objetivo é que **qualquer camada possa ser substituída** (um novo LLM, uma nova Skill, uma nova Tool, até um novo Runtime) sem que as demais precisem mudar uma linha de código — desde que a implementação nova respeite este contrato.

Isso é o que torna a migração entre as fases de hardware do projeto (protótipo → servidor → workstation) uma **troca de motor**, não uma reescrita de arquitetura. O contrato não sabe, e não deve saber, se está rodando numa RTX 5050 ou em algo maior.

---

# 2. Princípio de design: uma única Envelope

Toda comunicação entre camadas, em qualquer direção, usa a mesma estrutura base — a **Message Envelope**. Vale para pedido (`request`), resposta de sucesso (`result`), resposta de erro (`error`) **e** para o estado de espera por confirmação humana (`pending_confirmation` — ver seção 6.1).

```json
{
  "trace_id": "uuid-v4",
  "parent_id": "uuid-v4 | null",
  "layer_from": "runtime | agent | skill | tool",
  "layer_to": "runtime | agent | skill | tool",
  "timestamp": "ISO-8601",
  "type": "request | result | error | pending_confirmation",
  "target_id": "identificador do destinatário (ex: agent.researcher, skill.rag_search, tool.python_exec)",
  "payload": { },
  "context": { },
  "permissions": { },
  "meta": { }
}
```

### Por que cada campo existe

| Campo | Motivo |
|---|---|
| `trace_id` | Identifica a tarefa **raiz** completa. Todas as sub-chamadas (Agent chamando Skill, Skill chamando Tool) carregam o mesmo `trace_id`. É isso que permite reconstruir a árvore inteira de execução no log, do pedido do usuário até o resultado final. |
| `parent_id` | Identifica **quem chamou esta mensagem especificamente** (o `trace_id` da chamada imediatamente acima). Diferente do `trace_id`, que é fixo pra tarefa toda, o `parent_id` muda a cada nível da árvore. |
| `layer_from` / `layer_to` | Deixa explícito o contrato sendo usado, e permite validação automática — o Runtime nunca deveria receber uma mensagem `layer_to: tool` diretamente, por exemplo, isso indicaria uma violação de camada. |
| `payload` | O conteúdo específico da tarefa — muda de formato conforme a camada (detalhado nas seções 3-5). |
| `context` | Dados que **não são a tarefa em si**, mas que a camada de baixo pode precisar — ex: idioma preferido, formato de saída esperado, e (desde ADR-0008) o `domain` de isolamento — ver seção 7. |
| `permissions` | Nível de autorização da chamada — crítico pelo Princípio 10 do Manifesto. Detalhado na seção 8. |
| `meta` | Telemetria: latência, custo estimado de tokens, modelo usado, versão da Skill/Tool. Não afeta a lógica, só observabilidade. |

---

# 3. Contrato Runtime → Agent

O Runtime nunca executa lógica de domínio — ele só decompõe a tarefa (via Planner) e despacha para o Agent certo.

```json
{
  "trace_id": "a1b2c3d4",
  "parent_id": null,
  "layer_from": "runtime",
  "layer_to": "agent",
  "type": "request",
  "target_id": "agent.researcher",
  "payload": {
    "task_type": "research",
    "objective": "Localizar fontes primárias sobre um tópico X",
    "constraints": {
      "min_sources": 2,
      "preferred_language": ["pt-br", "en"]
    }
  },
  "context": {
    "project": "Segundo Cerebro"
  },
  "permissions": {
    "level": "read_only",
    "allowed_tools": ["tool.web_search", "tool.rag_query"]
  },
  "meta": { "priority": "normal" }
}
```

**Regra de contrato:** o Runtime nunca preenche `payload` com instruções de *como* fazer — apenas *o quê* e *sob quais restrições*. O "como" é responsabilidade do Agent (que decide quais Skills invocar).

---

# 4. Contrato Agent → Skill

Aqui mora a decisão mais importante do Manifesto (Princípio 2): **o Agent nunca calcula, nunca executa — ele só decide qual Skill invocar e com quais parâmetros.**

```json
{
  "trace_id": "a1b2c3d4",
  "parent_id": "req-agent-9f8e",
  "layer_from": "agent",
  "layer_to": "skill",
  "type": "request",
  "target_id": "skill.math_verify",
  "payload": {
    "operation": "verify_geometric_proof",
    "input": { "statement": "...", "proof_method": "..." }
  },
  "permissions": {
    "level": "execute_sandboxed",
    "allowed_tools": ["tool.sympy"]
  },
  "meta": { "timeout_ms": 5000 }
}
```

Note que o Agent **nunca vê os números crus** e nunca soma nada — ele só descreve a operação. Isso é o "LLM nunca calcula" aplicado literalmente na interface, não só como boa prática.

---

# 5. Contrato Skill → Tool

A Skill é a camada que sabe **qual sequência de Tools** resolve um tipo de operação. A Tool é burra e determinística — não tem contexto de negócio, só executa.

```json
{
  "trace_id": "a1b2c3d4",
  "parent_id": "req-skill-3c2b",
  "layer_from": "skill",
  "layer_to": "tool",
  "type": "request",
  "target_id": "tool.sympy",
  "payload": {
    "command": "verify_pythagorean",
    "args": { "a": 3, "b": 4, "expression": "a**2 + b**2 - c**2" }
  },
  "permissions": {
    "level": "execute_sandboxed",
    "sandbox": "docker://python-sandbox:latest"
  },
  "meta": { "timeout_ms": 2000 }
}
```

**Regra de contrato:** a Tool **nunca recebe linguagem natural** — só parâmetros estruturados. Se uma Skill está mandando um prompt em texto livre pra uma Tool, é sinal de que a fronteira Skill/Tool está mal desenhada.

---

# 6. Contrato de retorno (Result Envelope)

O caminho de volta usa a mesma envelope, invertendo `layer_from`/`layer_to`. Três variações de `type` são possíveis: `result`, `error`, ou `pending_confirmation` (seção 6.1).

```json
{
  "trace_id": "a1b2c3d4",
  "layer_from": "tool",
  "layer_to": "skill",
  "type": "result",
  "target_id": "tool.sympy",
  "payload": {
    "status": "success",
    "result": { "verified": true, "c": 5 }
  },
  "meta": { "latency_ms": 340 }
}
```

Em caso de erro, o formato é padronizado assim (a `make_error()` de `schemas.py` monta exatamente este formato):

```json
{
  "trace_id": "a1b2c3d4",
  "layer_from": "tool",
  "layer_to": "skill",
  "type": "error",
  "payload": {
    "status": "error",
    "error_code": "TOOL_TIMEOUT",
    "message": "Execucao excedeu 2000ms",
    "recoverable": true
  }
}
```

**Regra de contrato:** `error_code` sempre vem da lista fechada do enum `ErrorCode` (não texto livre) — esta é a lista **completa e atual**, implementada em código (a v0.1.1 documentava só um subconjunto):

| error_code | Significado | Ação típica |
|---|---|---|
| `TOOL_TIMEOUT` | Tool não respondeu a tempo | Retry com timeout maior, ou fallback |
| `PERMISSION_DENIED` | Camada de baixo recusou por política | Escalar para Validator/humano, nunca retry automático |
| `INVALID_INPUT` | Payload malformado | Bug do chamador — logar e não retry |
| `RESOURCE_EXHAUSTED` | VRAM/memória insuficiente | Sinal direto para o Scheduler decidir fila ou fallback de modelo menor |
| `MODEL_HALLUCINATION_SUSPECTED` | Critic Agent sinalizou inconsistência | Bloqueia promoção do resultado até revisão |
| `UNKNOWN_TARGET` | `target_id` sem rota conhecida no dispatcher | Nunca recoverable — é erro de configuração/roteamento, retry não resolve |
| `UPSTREAM_UNAVAILABLE` | Motor de LLM (ex: LM Studio) fora do ar | Tipicamente `recoverable: true` — vale retry |
| `HUMAN_REJECTED` | Humano recusou confirmar a operação pendente | Encerra o fluxo, não é erro técnico |
| `CONFIRMATION_NOT_FOUND` | `confirmation_id` inválido ou expirado | Bug do chamador ou timeout de UI — logar |

`recoverable` (booleano, `false` por padrão em `make_error()`) é quem decide se o RuntimeEngine tenta de novo — só marque `true` quando um retry puro e simples tem chance real de dar certo (timeout, indisponibilidade momentânea). Erros de roteamento ou de input nunca são recoverable, porque tentar de novo do mesmo jeito produz o mesmo erro.

## 6.1 O terceiro estado: `pending_confirmation`

Nem todo retorno é sucesso ou falha. Quando uma operação exige aprovação humana antes de executar (Princípio 10 do Manifesto), a camada retorna:

```json
{
  "trace_id": "a1b2c3d4",
  "type": "pending_confirmation",
  "payload": {
    "status": "pending_confirmation",
    "confirmation_id": "conf-42",
    "message": "Esta operação exige confirmação humana antes de executar."
  }
}
```

Isso pausa o fluxo — não é um erro a ser tratado com retry, é um estado legítimo esperando uma resposta humana (sim/não) referenciando o `confirmation_id`.

---

# 7. Isolamento por domínio (ADR-0008)

Alguns `target_id` operam sobre dados que **não podem se misturar entre si** — por exemplo, uma busca semântica não deve retornar resultados do domínio `courier` quando a pergunta é sobre `matematica`. Para esses alvos, `context.domain` é **obrigatório**, e restrito a um enum fechado:

```python
Domain = Literal["matematica", "courier", "eletronica"]
DOMAIN_REQUIRED_TARGETS = {"skill.rag_search", "tool.chromadb_add"}
```

A validação (`validate_domain_if_required()`) acontece na camada receptora — mesmo princípio da seção 8: nunca confiar que quem chamou preencheu certo. Se `context.domain` estiver ausente ou fora do enum, o dispatcher retorna `INVALID_INPUT` antes mesmo de rotear.

Adicionar um domínio novo é uma decisão arquitetural — exige ADR próprio, não uma edição livre da lista.

---

# 8. Permissions: como o contrato aplica o Princípio 10 do Manifesto

```json
"permissions": {
  "level": "read_only | execute_sandboxed | execute_with_confirmation | full_access",
  "allowed_tools": ["lista explícita"],
  "requires_human_confirmation": false,
  "sandbox": "docker://... | null"
}
```

**Ponto crítico de design:** a validação de `permissions` acontece **na camada receptora**, nunca confiando no que a camada de cima declarou. Mesmo que um Agent malicioso (ou um prompt injection vindo de um documento que ele leu) tente mandar `level: full_access` pra uma Skill de deleção de arquivo, a própria Skill deve ter sua própria whitelist de operações permitidas e rejeitar o que estiver fora dela. O contrato declara a intenção; a camada de baixo é quem garante a segurança de fato.

---

# 9. O que muda entre fases de hardware — e o que NUNCA muda

| Nunca muda (contrato) | Muda por trás do contrato (implementação) |
|---|---|
| Formato da Envelope (`trace_id`, `payload`, `permissions`...) | Qual modelo está atrás de `agent.researcher` |
| Regra de que Agent nunca calcula | Se uma Tool roda em container local ou em pool dedicado |
| Códigos de erro fechados (lista da seção 6) | Timeout default (mais curto com VRAM limitada) |
| Propagação de `trace_id`/`parent_id` | Se o log vai para SQLite ou um banco maior |
| Camadas não pulam hierarquia | Quantos Agents rodam em paralelo |

---

# 10. Exemplo de árvore completa (trace_id compartilhado)

```
trace_id: a1b2c3d4
│
├─ [runtime → agent.planner]      "decompor tarefa"
│   └─ result: [research, verify, write, document]
│
├─ [runtime → agent.researcher]   "buscar fontes"
│   └─ [agent → skill.rag_query]
│       └─ [skill → tool.chromadb_search]
│           └─ result: N documentos relevantes
│
├─ [runtime → agent.math]         "validar prova"
│   └─ [agent → skill.math_verify]
│       └─ [skill → tool.sympy]
│           └─ result: verified=true
│
├─ [runtime → agent.critic]       "revisar consistência"
│   └─ result: aprovado, sem contradições
│
└─ [runtime → agent.documentation] "escrever seção final"
    └─ result: markdown gerado, pronto para revisão humana
```

Com o `trace_id` fixo em toda a árvore, é possível reconstruir exatamente o que cada camada fez, quanto tempo levou, e onde — se algo der errado — o erro começou.

---

# 11. Verificação executável do contrato

Diferente da v0.1.1, este contrato não vive só neste documento — ele tem uma suíte de testes que o trava em código:

```
services/agent-os-api/app/schemas.py         ← implementação (fonte de verdade em código)
services/agent-os-api/tests/test_schemas.py  ← 26 testes cobrindo os pontos acima
```

Qualquer mudança de contrato (novo `error_code`, novo `type`, novo campo obrigatório) deve vir acompanhada de: (1) atualização deste documento, (2) atualização de `schemas.py`, (3) teste novo em `test_schemas.py` comprovando o comportamento. As três coisas juntas, não só uma — é assim que se evita a divergência que este próprio documento tinha na v0.1.1 (seção "Nota de sincronização" no topo).

---

# 12. Próximos passos

1. ~~Implementar um `EventBus` mínimo que force todas as chamadas a passarem pela Envelope~~ — resolvido pela própria validação Pydantic: `Envelope(...)` rejeita qualquer formato fora do contrato antes mesmo da lógica rodar.
2. Ao implementar o dispatcher da v2.0 (próxima fase), adicionar um teste que verifique que **todo** `target_id` presente no `AVAILABLE_TARGETS` do Planner tem uma rota correspondente — este era o bug mais grave encontrado na v0.1.1 (ver ADR referente, quando ratificado).
3. Formalizar a lista de `error_code` e o enum `Domain` como parte do JSON Schema exportável (`Envelope.model_json_schema()` do Pydantic já gera isso de graça, se algum consumidor externo precisar).