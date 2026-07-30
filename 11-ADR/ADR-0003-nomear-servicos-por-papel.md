# ADR-0003 — Nomear Serviços de Infraestrutura pelo Papel, não pela Tecnologia

Status: Aceito
Data: 2026-07-03
Documento técnico relacionado: `09-INFRASTRUCTURE/docker-compose.yml` (Fase 1), `09-INFRASTRUCTURE/Stack_por_Fase.md`

---

## Contexto

O ADR-0002 já define que várias peças de infraestrutura trocam de tecnologia entre fases (ChromaDB→Qdrant, SQLite→PostgreSQL, fila em memória→Redis→Kafka, LM Studio→vLLM). Isso resolve o problema *de qual tecnologia usar quando* — mas não resolve, sozinho, um problema mais sutil: **como o resto do sistema referencia esses serviços**.

Se o serviço no `docker-compose.yml` é nomeado literalmente pela tecnologia (`chromadb:`), esse nome vaza para todo lugar que precisa falar com ele — variáveis de ambiente (`VECTOR_DB_URL=http://chromadb:8000`), configuração do n8n, strings de conexão dentro do código do `agent-os-api`, documentação, scripts de backup. Um exemplo concreto do problema, se isso não fosse corrigido agora: ao migrar para Qdrant na Fase 2, seria necessário caçar e trocar a string `"chromadb"` em todos esses lugares — arquivo de configuração, código Python, docs — com risco real de esquecer uma ocorrência e gerar um erro silencioso de conexão em produção.

Esse é exatamente o tipo de acoplamento implícito que o Contrato de Interfaces (ADR-0001) já eliminou na camada de mensagens entre Runtime/Agent/Skill/Tool — mas que, sem esta decisão, continuaria existindo na camada de infraestrutura.

## Decisão

Todo serviço definido em `docker-compose.yml` (e, futuramente, em manifests do k3s na Fase 3) é nomeado pelo **papel funcional** que cumpre na arquitetura, nunca pela tecnologia específica por trás dele nesse momento.

Convenção adotada:

| Nome do serviço (papel) | Tecnologia Fase 1 | Tecnologia Fase 2 | Tecnologia Fase 3 |
|---|---|---|---|
| `vector-db` | ChromaDB | Qdrant | Qdrant (cluster) |
| `automation` | n8n | n8n | n8n |
| `search` | SearXNG | SearXNG | SearXNG (cluster) |
| `agent-os-api` | FastAPI + wrapper LM Studio | FastAPI + wrapper vLLM | FastAPI + gateway |
| `ocr-worker` | Tesseract | Tesseract | Tesseract + docTR (fallback GPU) |

Toda variável de ambiente, string de conexão e referência de código usa **exclusivamente o nome do papel** (`http://vector-db:8000`), nunca o nome da tecnologia. Quando uma tecnologia precisa ser identificada (ex: em logs, para fins de depuração), isso é feito via metadado explícito (`meta.engine_version` — já previsto no Contrato de Interfaces, ADR-0001), nunca via nome do serviço.

## Alternativas consideradas

1. **Nomear pela tecnologia (`chromadb`, `qdrant`) e aceitar o custo de migração como find-and-replace controlado.** Rejeitado: mesmo com ferramentas de refactor, o risco de uma referência esquecida (ex: dentro de um `.md` de documentação, ou de um script de backup que não é revisado com frequência) é desnecessário quando a solução alternativa custa zero a mais.

2. **Usar aliases de DNS dentro do Compose (nome real da tecnologia como container, mais um alias de rede com o nome do papel).** Rejeitado por adicionar uma camada extra de indireção sem benefício real sobre simplesmente nomear o serviço diretamente pelo papel — o Compose já suporta nome de serviço = nome de host DNS interno, então o alias seria redundante.

3. **Esperar até a Fase 2 para introduzir essa convenção**, já que na Fase 1 só há um serviço por papel mesmo. Rejeitado: o custo de nomear certo desde o início é zero, e esperar significaria repetir exatamente o problema que queremos evitar — migrar nomes espalhados pelo código depois que o sistema já estiver maior.

## Consequências

**Positivas:**
- Migração de tecnologia dentro de uma mesma fase, ou entre fases, se resume a alterar a definição de **um único serviço** no `docker-compose.yml` (a imagem, variáveis de configuração específicas da nova tecnologia) — nenhuma outra parte do sistema precisa ser tocada. Exemplo real: trocar `vector-db` de ChromaDB para Qdrant na Fase 2 é uma mudança de ~5 linhas no compose; sem esta convenção, seria uma busca por "chromadb" em potencialmente dezenas de arquivos.
- Reduz o risco de erro humano em migração (esquecer uma referência) a zero nas camadas que consomem o serviço, porque elas nunca souberam o nome da tecnologia em primeiro lugar.
- Facilita onboarding: quem lê o `docker-compose.yml` entende a **função** de cada peça na arquitetura antes de precisar saber qual tecnologia específica está rodando ali hoje.

**Negativas / trade-offs aceitos:**
- Perde-se a legibilidade imediata de "qual tecnologia está rodando" só olhando os nomes dos serviços — é preciso abrir a definição do serviço (a linha `image:`) para saber. Aceito porque essa informação pertence à documentação (`Stack_por_Fase.md`) e aos metadados de log, não ao nome do serviço.
- Exige disciplina contínua: se alguém, sob pressão de prazo, criar um novo serviço já nomeando pela tecnologia (ex: `redis:` em vez de `event-bus:` ao introduzir o Redis Streams na Fase 2), a convenção se rompe silenciosamente. Recomenda-se checar isso em qualquer revisão de `docker-compose.yml` ou manifest novo.
