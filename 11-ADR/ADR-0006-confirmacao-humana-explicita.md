# ADR-0006 — Confirmação Humana Explícita como Terceiro Estado da Envelope

Status: Aceito
Data: 2026-07-04
Origem: RFC-0001, item R-3
Documento técnico relacionado: `services/agent-os-api/app/schemas.py`, `db.py`, `main.py`

---

## Contexto

O campo `permissions.requires_human_confirmation` já existia na Message Envelope desde o ADR-0001, mas nenhum código realmente o verificava — o `dispatcher.py` só validava `permissions.level`. Na prática, isso era uma promessa de segurança não cumprida: uma Skill que declarasse `requires_human_confirmation: true` numa operação de risco (ex: apagar um arquivo, enviar uma comunicação em nome do usuário) seria executada de qualquer forma, sem pausa nenhuma.

Esse gap foi identificado ao comparar o Agent OS com o sistema de aprovação de comandos do OpenClaw, que exige validação humana explícita antes de executar ações classificadas como arriscadas (RFC-0001). A pergunta de design que isso levantou: onde, exatamente, essa checagem deveria acontecer, e o que fazer com uma Envelope que precisa "esperar" uma decisão que pode levar minutos, horas, ou sobreviver a um restart do container?

## Decisão

1. **Checagem no ponto de entrada, não no dispatcher.** A verificação de `requires_human_confirmation` acontece em `main.py`, antes de chamar `dispatch()` — nunca dentro de `dispatcher.py`. Isso preserva o ADR-0004 (o dispatcher só conhece roteamento e nível de permissão, não conceitos de fluxo de aprovação).

2. **Terceiro estado de Envelope: `pending_confirmation`.** Além de `request`, `result` e `error`, a Envelope agora aceita `type: "pending_confirmation"` — semanticamente distinto de erro, porque não significa "algo deu errado", significa "está tudo certo, falta uma decisão humana".

3. **Persistência em banco, não em memória.** A Envelope original inteira é serializada e guardada na tabela `pending_confirmation` — nunca numa fila em memória — porque a decisão de aprovar/rejeitar pode chegar depois de o processo ter reiniciado. Exemplo real: você inicia uma operação de risco à noite, o servidor reinicia por qualquer motivo antes de você aprovar pela manhã — com persistência em banco, a pendência sobrevive; em memória, ela se perderia silenciosamente.

4. **Dois endpoints novos e um de consulta:** `GET /v1/pending` (lista o que espera decisão), `POST /v1/confirm/{id}` (aprova e só então executa via `dispatch()`), `POST /v1/reject/{id}` (marca como rejeitado e devolve `error_code: HUMAN_REJECTED`, sem nunca chegar a rotear para a Tool).

## Alternativas consideradas

1. **Checar a confirmação dentro do `dispatcher.py`.** Rejeitado: exigiria dar acesso ao banco de dados para o dispatcher, que hoje é deliberadamente "burro" quanto a persistência (só roteia e valida nível de permissão) — misturaria duas responsabilidades que o ADR-0004 já separou de propósito.

2. **Usar o próprio campo `error` com um `error_code` do tipo `AWAITING_CONFIRMATION`.** Rejeitado: um Agent consumidor da resposta trataria isso como falha (`recoverable`, possível retry automático), quando na verdade é um estado de espera legítimo que exige uma ação humana específica, não uma nova tentativa da mesma chamada.

3. **Fila em memória para as pendências, dado que a Fase 1 já usa fila em memória para o Event Bus geral (ADR-0002).** Rejeitado especificamente para este caso: a fila de tarefas em memória assume que a tarefa vai ser processada em segundos ou minutos; uma confirmação humana pode legitimamente demorar horas, e não deveria se perder num restart do container só porque o Event Bus geral ainda não é persistente na Fase 1.

4. **Exigir um segundo campo de "motivo" obrigatório junto da confirmação (ex: `POST /v1/reject/{id}` com corpo explicando por quê).** Adiado: é uma melhoria de auditoria legítima, mas não bloqueava o gap de segurança em si — pode ser adicionado depois sem quebrar o contrato atual (o campo seria opcional/aditivo).

## Consequências

**Positivas:**
- Fecha um gap de segurança real que já existia silenciosamente no sistema — o campo do contrato agora tem efeito de verdade, não é só um metadado decorativo.
- O padrão é genérico: qualquer Tool futura (ex: `tool.filesystem_delete`, `tool.send_email_as_user`) ganha essa proteção automaticamente, só declarando `requires_human_confirmation: true` na Envelope — nenhuma mudança adicional em `dispatcher.py` é necessária.
- `GET /v1/pending` já funciona como um canal mínimo de aprovação para a Fase 1 (pergunta em aberto nº 1 do RFC-0001) — resolve o suficiente para uso pessoal sem exigir infraestrutura de notificação ainda.

**Negativas / trade-offs aceitos:**
- Duas chamadas de rede em vez de uma para operações que exigem confirmação (a chamada original que retorna `pending_confirmation`, mais a chamada de `/v1/confirm/{id}` depois) — aceito porque é exatamente o comportamento desejado: forçar uma pausa real, não uma formalidade.
- Não há expiração automática de pendências antigas nesta versão — uma `PendingConfirmation` esquecida fica "pending" para sempre até alguém decidir. Isso é um item de melhoria futura (ex: expirar após N dias e marcar como `expired`), não um bloqueio para a versão atual.
- `GET /v1/pending` não tem autenticação nesta fase — aceitável na Fase 1 (uso pessoal, rede local), mas precisa ser revisitado antes de qualquer exposição fora da rede local (relevante já a partir da Fase 2, quando outros serviços passam a rodar 24/7).
