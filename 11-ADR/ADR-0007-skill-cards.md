# ADR-0007 — Skill Cards: Metadado Obrigatório de Origem por Skill

Status: Aceito
Data: 2026-07-05
Origem: RFC-0001, item R-1
Documento técnico relacionado: `05-SKILLS/Skill_Template.md`

---

## Contexto

O OpenClaw exige, desde a atualização de skills do ClawHub, que cada skill instalada venha com um "Skill Card" documentando origem e propósito, e passe por escaneamento automatizado (SkillSpector) antes de ser aplicada. O Agent OS ainda não tem nenhuma Skill formalizada como entidade própria (hoje as capacidades existem como Tools chamadas diretamente pelo dispatcher), mas isso vai mudar assim que `skill.rag_search` e equivalentes forem implementados.

Sem um metadado de origem obrigatório desde o início, o risco é o mesmo já identificado no ADR-0005 para Tools: perder rastreabilidade de quem escreveu cada capacidade, quando, e se foi revisada — especialmente relevante se, no futuro, Skills de terceiros ou geradas com apoio de IA passarem a ser incorporadas.

## Decisão

Todo `Skill_Template.md` usado para criar uma Skill nova passa a exigir um bloco de metadados obrigatório no topo do arquivo:

```yaml
skill_id: skill.nome_da_skill
origem: interno | comunidade | terceiro
autor: <nome>
data_criacao: <data ISO>
revisado_por: null  # preenchido quando revisado
permissoes_minimas: <nível do Contrato de Interfaces>
tools_utilizadas: [lista de target_id de Tools]
escaneado_seguranca: false  # true só após checklist manual
```

Nesta fase, **não** construímos uma ferramenta automatizada de escaneamento (equivalente ao SkillSpector) — o campo `escaneado_seguranca` existe como lembrete visual manual, não como automação.

## Alternativas consideradas

1. **Construir escaneamento automatizado de segurança já agora.** Rejeitado por prematuro: com zero Skills formalizadas ainda, o custo de construir a ferramenta não se justifica frente ao benefício. Revisitar quando houver Skills de origem externa (comunidade/terceiro) sendo consideradas.
2. **Não formalizar metadado nenhum, tratar isso quando o número de Skills crescer.** Rejeitado: o custo de adicionar o bloco de metadados desde a primeira Skill é zero; adicionar retroativamente a dezenas de Skills existentes seria trabalho evitável.

## Consequências

**Positivas:**
- Toda Skill nova nasce com origem e permissão documentadas, sem exigir disciplina extra além de preencher um template já pronto.
- Prepara o terreno para automação de escaneamento futura sem exigir migração de dados: o campo já existe, só passa a ser preenchido automaticamente em vez de manualmente.

**Negativas / trade-offs aceitos:**
- Nenhuma proteção automática existe ainda — um metadado `escaneado_seguranca: false` esquecido não impede a Skill de rodar. Isso é aceitável na Fase 1 (uso pessoal, Skills majoritariamente internas), mas precisa ser revisitado antes de aceitar Skills de terceiros de fato.
