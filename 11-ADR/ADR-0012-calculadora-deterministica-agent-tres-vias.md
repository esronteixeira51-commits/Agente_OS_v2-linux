# ADR-0012 — Calculadora Determinística e Decisão de Três Vias no Agent

Status: Aceito
Data: 2026-07-08
Origem: Incidente real — LLM "narrando" matemática sem verificação (ver `13-STANDARDS/Testing.md`, Incidente 5)
Documento técnico relacionado: `services/agent-os-api/app/calculator_engine.py`, `dispatcher.py`, `agents.py`

---

## Contexto

O `agent.researcher` decidia originalmente entre apenas dois caminhos: buscar contexto na base de conhecimento, ou responder direto. Perguntas matemáticas caíam no caminho "direto", deixando o LLM "narrar" a conta em texto livre — inclusive gerando blocos de código formatados como se fossem executados, sem execução real por trás.

Um teste real expôs a gravidade do problema: pedindo o cálculo de `123456789^123456` com contagem de dígitos, o modelo produziu um número de dígitos sem qualquer relação com a resposta correta — uma alucinação completa e confiante, sem nenhum sinal de incerteza. Isso viola diretamente o Princípio 2 do Manifesto ("cálculos nunca serão feitos pelo LLM").

## Decisão

### 1. Nova Tool: `tool.calculator`

Avalia expressões aritméticas via `ast.parse` e percurso manual da árvore sintática, com allowlist explícita e fechada de operadores (`+ - * / // % **`) e funções (`sqrt, abs, round, min, max, pow`). **Nunca usa `eval()`/`exec()` do Python** sobre a expressão recebida — qualquer nó da árvore fora da allowlist (chamadas de função não listadas, nomes livres, atributos, etc.) é rejeitado antes de qualquer execução.

Quando o resultado é um número inteiro, a Tool também calcula, de forma determinística, a contagem de dígitos, os primeiros/últimos 25 dígitos, e a soma de todos os dígitos — precisamente as operações que o incidente relatado mostrou que o LLM executa mal para números grandes.

Inclui proteção contra negação de serviço por magnitude: antes de executar uma potenciação, o tamanho estimado do resultado é calculado via logaritmo; se exceder `MAX_RESULT_DIGITS` (2 milhões), a operação é rejeitada antes de consumir tempo/memória reais.

### 2. `agent.researcher` decide entre três vias, não duas

`"search"` (busca contexto), `"calculate"` (traduz a pergunta para uma expressão pura e delega à `tool.calculator`), `"direct"` (responde sem etapa intermediária). Crucialmente, quando a via é `"calculate"`, o LLM só **traduz** linguagem natural para uma expressão matemática — uma tarefa de tradução, não de cálculo. Quem calcula é sempre a Tool.

Na formatação da resposta final, o LLM recebe os fatos já calculados (valor, contagem de dígitos, etc.) como texto de sistema, com instrução explícita de nunca recalcular ou apresentar um valor diferente do fornecido.

## Alternativas consideradas

1. **Usar `eval()` do Python diretamente sobre a expressão, confiando que o LLM só gera expressões inofensivas.** Rejeitado terminantemente: `eval()` executa qualquer código Python, incluindo acesso a sistema de arquivos, rede, ou processos — um risco real mesmo confiando no LLM, porque alucinação ou prompt injection poderiam gerar uma expressão maliciosa (validado no teste de segurança: `__import__('os').system(...)` foi corretamente bloqueado pela allowlist, mas seria executado sem ela).
2. **Integrar com o `js-code-sandbox` nativo do LM Studio**, que já existe e tem aprovação humana embutida na interface. Rejeitado: só é acionado quando a chamada à API inclui explicitamente uma lista de `tools` no formato function-calling da OpenAI; além disso, ficaria fora do dispatcher — sem `trace_id`, sem entrada no `execution_log`, quebrando a auditabilidade central do Contrato de Interfaces (ADR-0001); e amarraria essa capacidade especificamente ao LM Studio, quebrando a portabilidade para vLLM na Fase 2 (ADR-0002).
3. **Não limitar a magnitude do resultado de potenciações.** Rejeitado após descoberta concreta durante o desenvolvimento: uma expressão como `9 ** 999999999999` tentaria gerar um número com bilhões de dígitos, risco real de esgotamento de memória/CPU — categoria de ataque (negação de serviço por magnitude) distinta da execução de código arbitrário, mas igualmente real.

## Consequências

**Positivas:**
- Fecha a lacuna mais visível entre o que o Manifesto promete ("LLM nunca calcula") e o que o sistema realmente garantia até este ponto — agora há prova concreta (teste automatizado) de que cálculos passam por verificação determinística.
- A allowlist de AST é auditável linha por linha — qualquer extensão futura de funções permitidas é uma mudança pequena e visível, não uma reavaliação de superfície de ataque inteira.
- A separação "LLM traduz, Tool calcula" generaliza para qualquer necessidade futura de processamento determinístico (ex: análise estatística, conversão de unidades) sem precisar reinventar o padrão.

**Negativas / trade-offs aceitos:**
- A allowlist é deliberadamente pequena — expressões que precisem de funções matemáticas fora da lista (trigonometria, logaritmos, etc.) falham com `INVALID_INPUT` até serem adicionadas explicitamente. Aceito: expandir a allowlist é seguro e rápido quando a necessidade aparecer; começar restritivo e crescer é mais seguro que o inverso.
- `MAX_RESULT_DIGITS` é um limite arbitrário (2 milhões) — pode eventualmente ser baixo demais para um caso de uso legítimo futuro. Revisável por configuração, não é um limite rígido de arquitetura.
- A decisão de três vias no `agent.researcher` ainda depende da tradução do LLM ser uma expressão sintaticamente válida; traduções malformadas degradam para `"search"` (padrão seguro já estabelecido), nunca travam o Agent — mas isso significa que uma pergunta matemática mal traduzida pode, ocasionalmente, ser respondida sem cálculo determinístico nenhum, silenciosamente. Mitigação futura possível: expor esse caso de degradação de forma mais visível no retorno do Agent.
