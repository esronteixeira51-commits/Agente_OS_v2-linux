# ADR-0009 — Skills Versionadas como Código, com Convenção de Commit Dedicada

Status: Aceito
Data: 2026-07-05
Origem: RFC-0001, item R-5
Documento técnico relacionado: `13-STANDARDS/Naming.md` (a ser criado)

---

## Contexto

A prática recomendada pela comunidade OpenClaw é tratar definições de skill como código — versionadas em Git, com revisão de mudanças e rollback possível. O Agent OS já usa Git para todo o código, então isso não é uma capacidade nova — é a ausência de uma convenção explícita de como usar essa capacidade já existente especificamente para Skills.

Sem essa convenção, o risco é prático e comum: uma mudança de comportamento numa Skill fica misturada num commit genérico junto com outras alterações não relacionadas, tornando quase impossível responder, meses depois, "quando exatamente a `skill.rag_search` mudou de comportamento" sem vasculhar manualmente um histórico grande.

## Decisão

Toda Skill nova ou modificada exige um commit próprio, não misturado com mudanças de Tool, Agent ou infraestrutura, seguindo o formato:

```
skill(<skill_id_sem_prefixo>): <descrição curta da mudança>

Refs: <ADR relacionado, se houver>
```

Exemplo real:

```
skill(rag_search): adiciona filtro de domínio obrigatório

Refs: ADR-0008 (isolamento de workspace por domínio)
```

Isso permite consultas diretas de histórico por Skill, por exemplo `git log --grep="skill(rag_search)"`, sem precisar vasculhar commits não relacionados.

## Alternativas consideradas

1. **Não formalizar convenção, confiar na disciplina natural.** Rejeitado: a experiência já documentada no projeto (ex: a necessidade de registrar ADRs formalmente em vez de confiar em "lembrar depois") mostra que convenções não escritas se perdem sob pressão de prazo.
2. **Usar tags de Git em vez de prefixo de mensagem de commit.** Rejeitado por fricção maior: exigiria uma tag por mudança de Skill, enquanto o prefixo na mensagem de commit já é pesquisável nativamente pelo `git log`, sem comando adicional.

## Consequências

**Positivas:**
- Rastreamento de mudança de comportamento por Skill vira uma busca de segundos, não uma investigação manual.
- Custo de implementação é zero — é convenção de processo, não código novo.

**Negativas / trade-offs aceitos:**
- Depende inteiramente de disciplina humana para ser seguida — não há enforcement automático (ex: git hook rejeitando commits fora do padrão) nesta fase. Pode ser revisitado como melhoria futura se a convenção começar a ser esquecida na prática.
