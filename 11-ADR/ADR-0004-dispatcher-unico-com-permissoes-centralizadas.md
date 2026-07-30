# ADR-0004 — Dispatcher Único com Tabela Central de Permissões

Status: Aceito
Data: 2026-07-04
Documento técnico relacionado: `services/agent-os-api/app/dispatcher.py`, `01-ARCHITECTURE/Contrato_Interfaces.md`

---

## Contexto

O `agent-os-api` precisa receber Message Envelopes (ADR-0001) vindas de qualquer camada e rotear cada uma para o handler certo — hoje só `tool.llm_call`, mas amanhã também `skill.rag_search`, `skill.math_verify`, `tool.sympy`, e assim por diante conforme o Agent OS cresce.

Existiam duas formas óbvias de organizar isso: (a) um endpoint HTTP separado por `target_id` (ex: `POST /v1/tool/llm_call`, `POST /v1/skill/rag_search`), cada um checando sua própria permissão internamente; ou (b) um único endpoint (`POST /v1/dispatch`) que recebe qualquer Envelope e decide internamente o que fazer.

O risco concreto da opção (a): a checagem de permissão (Contrato de Interfaces, seção 7 — "a validação acontece na camada receptora, nunca confiando no que a camada de cima declarou") ficaria duplicada em cada endpoint. Isso é exatamente o tipo de repetição que, na prática, leva a inconsistência — por exemplo, alguém adiciona um endpoint novo sob pressão de prazo e esquece de validar `permissions.level`, criando um furo de segurança silencioso (Manifesto, Princípio 10: "segurança sempre vem primeiro").

## Decisão

Adotar um **único endpoint** (`POST /v1/dispatch`) que recebe qualquer Envelope, e centralizar toda a lógica de roteamento e validação de permissão em `dispatcher.py`, numa única estrutura de dados:

```python
_MIN_PERMISSION_LEVEL: dict[str, set[str]] = {
    "tool.llm_call": {"execute_sandboxed", "execute_with_confirmation", "full_access"},
}
```

Toda `target_id` nova (ex: ao adicionar `skill.rag_search` no futuro) exige apenas uma nova entrada nesse dicionário e um novo `if` de roteamento dentro da função `dispatch()` — nunca um endpoint HTTP novo, nunca uma checagem de permissão reimplementada em outro lugar.

## Alternativas consideradas

1. **Um endpoint HTTP por target_id**, com validação de permissão feita dentro de cada handler individualmente. Rejeitado: multiplica o código de checagem de segurança por N endpoints, aumentando a chance de um deles ficar desatualizado ou ser esquecido quando a política de permissão mudar.

2. **Usar um framework de API Gateway pronto (ex: Kong, Traefik) para fazer a validação de permissão antes mesmo de chegar no FastAPI.** Rejeitado para a Fase 1: adiciona uma peça de infraestrutura extra (mais um serviço no compose, mais uma camada de configuração) para um problema que, no volume atual (um usuário, poucos `target_id`), o próprio dispatcher resolve com uma função de 10 linhas. Reavaliar na Fase 3, quando `10-API/` prevê um gateway de fato (ver `Stack_por_Fase.md`).

3. **Deixar a validação de permissão embutida em cada Skill/Tool, sem checagem central no agent-os-api.** Rejeitado: violaria diretamente a seção 7 do Contrato de Interfaces, que exige que a camada receptora imediata valide — não a camada final de execução, que pode estar várias chamadas adiante e já ter processado dados antes de perceber que não deveria.

## Consequências

**Positivas:**
- Adicionar uma rota nova ao Agent OS é uma mudança previsível e pequena: uma linha no dicionário de permissões, um bloco `if` no `dispatch()`. Exemplo real esperado no curto prazo: ao introduzir `skill.rag_search`, basta adicionar `"skill.rag_search": {"read_only", "execute_sandboxed", ...}` e um handler `_handle_rag_search()`, sem tocar em `main.py` nem criar rota HTTP nova.
- Toda tentativa de acesso indevido passa pelo mesmo ponto de checagem, o que torna auditoria e testes de segurança mais simples — um único teste automatizado cobre a política de permissão de todos os `target_id` de uma vez, em vez de precisar testar cada endpoint separadamente.
- Mantém `main.py` enxuto e estável — ele não muda quando novas capacidades são adicionadas ao sistema, só `dispatcher.py` cresce.

**Negativas / trade-offs aceitos:**
- `dispatcher.py` tende a crescer e virar um arquivo grande conforme o número de `target_id` aumenta — na Fase 2/3, pode ser necessário quebrar o dicionário de permissões e os handlers em módulos separados por domínio (ex: `dispatcher_math.py`, `dispatcher_rag.py`), mantendo `dispatch()` como orquestrador fino. Isso não é uma mudança de contrato, só de organização interna do código.
- Um único endpoint HTTP significa que não há, por padrão, rate-limiting ou logging diferenciado por tipo de operação no nível do FastAPI/HTTP — se isso for necessário, precisa ser implementado dentro do próprio dispatcher (ex: por `target_id`), não delegado ao roteamento HTTP.
