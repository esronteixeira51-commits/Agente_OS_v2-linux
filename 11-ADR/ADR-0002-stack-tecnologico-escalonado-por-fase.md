# ADR-0002 — Stack Tecnológico Escalonado por Fase

Status: Aceito
Data: 2026-07-03
Documento técnico completo: `09-INFRASTRUCTURE/Stack_por_Fase.md`

---

## Contexto

O projeto tem três estágios de hardware já planejados em `Infraestrutura_Projeto.md` (protótipo RTX 5050 8GB → servidor 2× RTX 5090 64GB → workstation 4× RTX 6000 Blackwell 384GB), com naturezas de problema diferentes em cada um: a Fase 1 é limitada por VRAM e concorrência zero; a Fase 2 introduz paralelismo real entre GPUs; a Fase 3 introduz múltiplos usuários e cargas de produção.

Escolher uma única tecnologia "de uma vez para sempre" em cada categoria (LLM serving, vector DB, fila de eventos, etc.) forçaria ou sobre-engenharia prematura na Fase 1 (ex: montar Kafka pra um único usuário) ou sub-dimensionamento na Fase 3 (ex: ChromaDB embutido tentando servir múltiplos consumidores concorrentes).

## Decisão

Adotar stack diferente por categoria em cada fase, mantendo fixo apenas o que **não** depende de escala: formato da Message Envelope (ADR-0001), Docker como unidade de empacotamable, FastAPI como camada de API, Git para versionamento.

Resumo das trocas decididas (detalhamento completo no documento técnico):

| Categoria | Fase 1 | Fase 2 | Fase 3 |
|---|---|---|---|
| LLM Serving | LM Studio | vLLM (tensor-parallel=2) | vLLM/TensorRT-LLM (tensor-parallel=4) |
| Event Bus | Fila em memória | Redis Streams | Apache Kafka |
| Vector DB | ChromaDB | Qdrant | Qdrant (cluster) |
| Banco relacional | SQLite | PostgreSQL | PostgreSQL + réplica |
| Orquestração de containers | Docker Compose | Docker Compose (GPU pinning) | k3s |

## Alternativas consideradas

1. **Adotar Kubernetes completo desde a Fase 1.** Rejeitado: fricção operacional alta demais para um único node/usuário; k3s só se justifica quando há necessidade real de scheduling multi-node ou multi-usuário (Fase 3).
2. **Manter LM Studio em todas as fases.** Rejeitado: LM Studio serve uma sessão por vez — não sustenta múltiplos Agents chamando o modelo em paralelo, que é exatamente o ganho esperado das GPUs adicionais na Fase 2.
3. **Ir direto para PostgreSQL + Qdrant já na Fase 1**, evitando duas migrações. Rejeitado: adiciona serviços e pontos de falha desnecessários enquanto o volume de dados e concorrência da Fase 1 não justificam — SQLite/ChromaDB embutidos resolvem com fricção zero nesse estágio.

## Consequências

**Positivas:**
- Cada fase usa a ferramenta com menor fricção operacional para o problema *daquele* estágio, evitando tanto sobre-engenharia quanto sub-dimensionamento.
- Se o código da Fase 1 usar SQLAlchemy (ORM) e um wrapper FastAPI sobre o LM Studio desde o início, as migrações Fase 1→2 ficam restritas a troca de connection string / endpoint, não reescrita de lógica.

**Negativas / trade-offs aceitos:**
- Duas migrações de infraestrutura ao longo do projeto (SQLite→PostgreSQL, ChromaDB→Qdrant, Redis→Kafka) em vez de uma escolha única — custo aceito em troca de não pagar complexidade operacional antes da hora.
- Exige disciplina de abstração desde a Fase 1 (não chamar LM Studio direto das Skills, não escrever SQL cru fora do ORM) — se essa disciplina não for seguida, o custo de migração predito acima deixa de valer.
