# ADR-0011 — Fixar Versões Exatas de Imagem Docker, Nunca Usar `:latest`

Status: Aceito
Data: 2026-07-06
Origem: Incidente real durante o teste de fumaça — falha HTTP 400 em `tool.chromadb_add`/`skill.rag_search`
Documento técnico relacionado: `docker-compose.yml`, `services/agent-os-api/requirements.txt`

---

## Contexto

O `docker-compose.yml` fixava versões exatas para toda dependência Python (`fastapi==0.115.0`, `pydantic==2.9.2`, etc.), mas o serviço `vector-db` usava `image: chromadb/chroma:latest` — uma inconsistência introduzida sem intenção.

Isso causou uma falha real: o cliente Python do ChromaDB foi fixado em `chromadb==0.5.23` no momento em que o `chromadb_client.py` foi escrito. Como o servidor usava `:latest`, ele continuou avançando de versão sozinho a cada `docker compose pull`/rebuild, enquanto o cliente ficou parado. A equipe do ChromaDB documenta oficialmente mudanças que quebram compatibilidade entre versões de cliente e servidor (por exemplo, a forma como tenant/database são validados mudou entre a API v1 e v2). O resultado prático: `tool.chromadb_add` e `skill.rag_search` falhavam com HTTP 400 no teste de fumaça, mesmo com o `docker-compose.yml`, o `dispatcher.py` e o payload da requisição todos corretos — a causa não estava em nenhum código nosso, estava na divergência de versão entre as duas pontas.

## Decisão

Toda imagem Docker de serviço especializado (ChromaDB hoje; qualquer banco de dados, motor de busca, ou serviço externo equivalente no futuro) usa uma **tag de versão exata**, nunca `:latest`, seguindo a mesma disciplina já aplicada às dependências Python.

Regra derivada: sempre que uma versão de cliente (biblioteca Python) para um serviço externo for fixada num `requirements.txt`, a versão da imagem Docker desse mesmo serviço no `docker-compose.yml` deve ser fixada para a **versão correspondente/compatível**, não deixada solta.

## Alternativas consideradas

1. **Manter `:latest` e resolver incompatibilidades quando aparecerem.** Rejeitado: é exatamente o que já aconteceu — um erro silencioso (HTTP 400 genérico, sem nenhuma mensagem indicando "incompatibilidade de versão") que consumiu tempo de diagnóstico real, quando poderia ter sido evitado com uma linha de configuração.
2. **Fixar só o cliente Python, deixar o servidor em `:latest` mas testar antes de cada uso.** Rejeitado: testar manualmente antes de cada uso não escala e não é confiável — o mesmo `docker-compose.yml` rodando em duas máquinas diferentes, em datas diferentes, poderia baixar versões de servidor diferentes uma da outra, tornando o comportamento do sistema não-determinístico entre ambientes.
3. **Atualizar o cliente Python para a versão mais recente do ChromaDB, em vez de fixar o servidor na versão antiga do cliente.** Considerado, mas não escolhido como correção imediata: exigiria validar se a API do `chromadb_client.py` muda entre versões (ela muda, conforme documentado nas mudanças de tenant/database). Fixar ambos na versão já testada (`0.5.23`) é a correção de menor risco agora; migrar os dois juntos para uma versão mais nova fica como melhoria futura deliberada, não como correção emergencial.

## Consequências

**Positivas:**
- Elimina uma classe inteira de bugs "funciona hoje, quebra amanhã sem nenhuma mudança de código nossa" — o comportamento do `vector-db` fica congelado até uma decisão explícita de atualizar.
- Mesma reprodutibilidade entre ambientes (sua máquina, um colaborador futuro, a Fase 2) que já buscamos com as versões Python fixadas.

**Negativas / trade-offs aceitos:**
- Perde-se correções de bugs e melhorias de performance que o ChromaDB lança depois da versão fixada — aceito conscientemente; atualizar vira uma decisão deliberada e testada, não um acidente de `docker compose pull`.
- Exige lembrar de atualizar a versão fixada manualmente de tempos em tempos (nenhum mecanismo automático avisa que uma versão nova saiu) — risco aceito por ora, revisável se o projeto crescer a ponto de justificar um bot de atualização de dependências.
