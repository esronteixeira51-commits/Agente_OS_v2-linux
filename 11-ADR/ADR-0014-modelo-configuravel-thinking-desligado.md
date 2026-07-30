# ADR-0014 — Modelo de LLM configurável e thinking mode desligado para tool calling confiável

## Status
Aceito

## Contexto

Durante a validação de ponta a ponta do Agent OS v2.0 (Fase 8, LM Studio
real), dois problemas relacionados apareceram:

1. **O identificador do modelo estava hardcoded** dentro de `app.agents`
   (`"lmstudio-community/qwen2.5-7b-instruct"`), violando o próprio
   princípio documentado em `app.llm_client` de que só esse arquivo
   deveria saber qual motor/modelo está rodando. O identificador
   hardcoded não batia com nenhum dos modelos carregados no LM Studio do
   usuário (`qwen/qwen3-8b`, `qwen2.5-7b-instruct`,
   `text-embedding-nomic-embed-text-v1.5`).
2. **O LM Studio, ao receber um identificador de modelo não reconhecido,
   não retorna erro — ele silenciosamente atende a chamada com outro
   modelo carregado.** Isso mascarou o problema (1) por várias rodadas
   de teste: as respostas vinham normalmente, só que de um modelo
   diferente do que o payload pedia, sem nenhum aviso.
3. Com o modelo de fato usado sendo `qwen/qwen3-8b` (um modelo de
   raciocínio, que gera um rascunho de "pensamento" antes da resposta
   final), o `agent.researcher` ocasionalmente respondia perguntas de
   multiplicação simples **sem chamar `tool.calculator`** — o modelo
   "decidia" durante o próprio raciocínio interno que não precisava da
   ferramenta, calculando mentalmente. Isso viola o Manifesto, Princípio
   2 ("cálculos nunca serão feitos pelo LLM").

## Decisão

Duas mudanças, tratadas juntas por serem a causa e o sintoma do mesmo
problema:

1. **`default_model` passa a ser uma configuração (`Settings`,
   `config.py`), nunca mais hardcoded em `app.agents`.** Qualquer troca
   de modelo é uma mudança de variável de ambiente
   (`DEFAULT_MODEL` no `.env`), não de código.
2. **O modo de raciocínio (thinking) do modelo em uso
   (`qwen/qwen3-8b`) é desligado no lado do LM Studio**, editando o
   prompt template (Jinja) do modelo para incluir
   `{%- set enable_thinking = false %}` no topo. Confirmado
   experimentalmente: com o thinking desligado, o modelo passou a
   chamar `tool.calculator` de forma consistente, inclusive para
   multiplicações de números grandes (testado com `12345 × 67890`).

Mantém-se a decisão de continuar usando `qwen3-8b` como modelo
principal (não trocar para um modelo sem capacidade de raciocínio) —
um dos princípios do projeto é a liberdade de usar qualquer modelo. A
correção atua sobre *como* o modelo é operado (thinking ligado/desligado
por chamada), não sobre *qual* modelo é usado.

## Alternativas consideradas

- **Forçar `tool_choice="required"` em toda chamada do Planner/Agents.**
  Rejeitado como primeira solução: obrigaria o modelo a chamar alguma
  ferramenta mesmo em perguntas que não precisam de nenhuma (ex: "qual
  é a capital da França?"), desperdiçando uma chamada e possivelmente
  produzindo uma tool call artificial. Fica registrado como próxima
  tentativa caso o thinking desligado deixe de ser suficiente no
  futuro (ex: com um modelo diferente que não tenha esse soft switch).
- **Trocar para `qwen2.5-7b-instruct` (sem capacidade de raciocínio).**
  Rejeitado por decisão explícita do usuário — contraria o princípio de
  liberdade de escolha de modelo que o projeto pretende preservar.
- **Reforçar só o system prompt, sem desligar o thinking.** Aplicado
  como reforço complementar (ver `app.agents._RESEARCHER_SYSTEM_PROMPT`),
  mas não foi testado como suficiente sozinho — o desligamento do
  thinking foi o que resolveu de forma confirmada.

## Consequências

- **Positivas:** tool calling confiável e determinístico com o modelo
  de raciocínio preferido do usuário; configuração de modelo agora é
  operacional (variável de ambiente), não exige rebuild de imagem nem
  edição de código para trocar de modelo.
- **Negativas / trade-offs aceitos:** o ajuste de thinking mode é feito
  no lado do LM Studio (edição manual do prompt template), não faz
  parte do código versionado do Agent OS — se o usuário recarregar o
  modelo do zero ou trocar de máquina, precisa reaplicar essa
  configuração manualmente. Documentado em
  `09-INFRASTRUCTURE/Preparacao_Ambiente_Linux.md` para não se perder.
- **Risco residual conhecido, não resolvido por este ADR:** o LM Studio
  continua aceitando silenciosamente um `model` não reconhecido e
  substituindo por outro carregado, sem erro. Se `DEFAULT_MODEL` for
  configurado errado no futuro, o sistema não vai avisar — só vai
  "funcionar" com um modelo diferente do esperado, do mesmo jeito que
  mascarou o problema (1) aqui. Uma validação de startup (conferir
  `DEFAULT_MODEL` contra `GET /v1/models` do LM Studio) é uma melhoria
  futura ainda não implementada.