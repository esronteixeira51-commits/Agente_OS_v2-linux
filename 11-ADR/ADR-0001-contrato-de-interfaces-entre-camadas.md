# ADR-0001 — Contrato de Interfaces entre Camadas

Status: Aceito
Data: 2026-07-03
Documento técnico completo: `01-ARCHITECTURE/Contrato_Interfaces.md`

---

## Contexto

O Agent OS é dividido em camadas (Runtime, Agents, Skills, Tools) que precisam se comunicar sem se acoplar diretamente — isso é exigido pelo Manifesto (Princípio 6: "todo módulo deve ser substituível"; Princípio Arquitetural: "nenhuma camada poderá acessar outra ignorando as interfaces oficiais").

Sem um formato de mensagem padronizado, cada par de camadas tenderia a inventar seu próprio formato ad-hoc conforme fosse implementado — o que na prática recria o acoplamento que a separação em camadas deveria evitar. Exemplo concreto do problema: se o Agent Researcher chamasse `skill.rag_search` passando só uma string de texto livre, e o Agent Math chamasse `skill.math_verify` passando um dicionário com chaves diferentes a cada vez, não haveria como auditar, versionar ou validar essas chamadas de forma automática.

## Decisão

Adotar uma **Message Envelope única** para toda comunicação entre camadas, em qualquer direção (pedido e resposta), contendo sempre: `trace_id`, `parent_id`, `layer_from`, `layer_to`, `timestamp`, `type`, `target_id`, `payload`, `context`, `permissions`, `meta`.

Regras fixas do contrato:
- O `trace_id` é único por tarefa raiz e propagado por toda a árvore de chamadas.
- `permissions` é sempre validado pela camada **receptora**, nunca confiando na declaração da camada de cima (defesa contra prompt injection).
- Erros usam `error_code` de uma lista fechada e versionada, nunca texto livre.

## Alternativas consideradas

1. **Cada camada define seu próprio formato de mensagem.** Rejeitado: recria acoplamento implícito e impede auditoria automática via `trace_id` compartilhado.
2. **Usar um framework de agentes pronto (ex: LangGraph, CrewAI) que já define seu próprio protocolo interno.** Rejeitado por ora: amarraria a arquitetura a decisões de design de terceiros, contrariando o Princípio 7 ("a arquitetura vale mais que a implementação") — pode ser revisitado como *implementação* de uma camada específica no futuro, nunca como substituto do contrato em si.
3. **gRPC com Protobuf em vez de JSON.** Rejeitado para a Fase 1 por fricção operacional (exige compilação de schema, mais difícil de depurar manualmente); reavaliar na Fase 3 se latência de serialização se tornar gargalo mensurável.

## Consequências

**Positivas:**
- Migração de hardware (Fase 1 → 2 → 3) não exige reescrever Agents/Skills, só trocar o transporte da Envelope (ex: fila em memória → Redis Streams → Kafka).
- `trace_id` propagado desde a Fase 1 se converte diretamente em spans do OpenTelemetry na Fase 3, sem retrabalho de instrumentação.

**Negativas / trade-offs aceitos:**
- Overhead de serialização JSON em cada chamada, mesmo entre Skill e Tool na mesma máquina — aceito conscientemente em troca de simplicidade de depuração na Fase 1.
- Exige disciplina: qualquer chamada direta entre camadas que pule a Envelope (ex: um Agent chamando uma Tool sem passar pela Skill) quebra a auditabilidade — precisa ser pego em code review, não é impedido automaticamente até o EventBus da Fase 1 forçar isso estruturalmente (ver próximos passos do documento técnico).
