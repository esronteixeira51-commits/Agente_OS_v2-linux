# ADR-0005 — Tools Especialistas Não Falam o Dialeto de Message Envelope

Status: Aceito
Data: 2026-07-04
Documento técnico relacionado: `services/ocr-worker/`, `services/agent-os-api/app/dispatcher.py`, `01-ARCHITECTURE/Contrato_Interfaces.md`

---

## Contexto

Ao construir o `ocr-worker`, existiam duas formas possíveis de desenhar sua API: (a) o `ocr-worker` recebe e devolve a Message Envelope completa diretamente (`trace_id`, `layer_from`, `layer_to`, `permissions`, etc. — o mesmo formato que trafega entre Runtime/Agent/Skill/Tool), ou (b) o `ocr-worker` expõe uma API mínima e estruturada (`{file_path, language}` → `{status, text, page_count}`), e é o `agent-os-api` quem traduz entre essa API e a Envelope do Contrato de Interfaces.

O exemplo concreto que expôs o problema: se o `ocr-worker` recebesse a Envelope inteira, ele precisaria entender e validar `permissions.level`, montar `trace_id`/`parent_id` corretamente na resposta, e conhecer a lista fechada de `error_code` do Contrato — ou seja, ele precisaria embutir uma cópia da lógica de contrato que já existe centralizada no `dispatcher.py` (ADR-0004). Isso duplicaria conhecimento do contrato em dois lugares, e criaria a pergunta: se o formato da Envelope mudar de versão no futuro, o `ocr-worker` (e todo Tool especialista futuro — Whisper, SymPy, etc.) precisaria ser atualizado junto?

## Decisão

**Tools especialistas** (serviços que executam uma capacidade técnica isolada — OCR, transcrição de áudio, cálculo matemático) expõem uma **API própria, simples e estruturada**, sem qualquer conhecimento da Message Envelope, `trace_id`, `permissions` ou `error_code`. Apenas parâmetros de entrada e resultado da operação, no formato mais natural para aquela tecnologia.

A tradução entre a Envelope (usada em todo o resto do Agent OS) e essa API simples acontece **exclusivamente** dentro de `dispatcher.py`, via um par cliente/handler por Tool — ex: `ocr_client.py` + `_handle_ocr_extract()` para o `ocr-worker`, seguindo exatamente o mesmo padrão já usado para `llm_client.py` + `_handle_llm_call()`.

Regra derivada: **nenhum Tool especialista importa ou referencia `schemas.py`** (onde a Envelope é definida) — se isso acontecer, é sinal de que a fronteira foi desenhada errado.

## Alternativas consideradas

1. **Tool especialista recebe e devolve a Envelope completa.** Rejeitado: duplicaria a lógica de validação de contrato (permissões, `trace_id`, `error_code`) em cada novo Tool, e acoplaria a evolução de cada Tool à evolução do formato da Envelope — uma mudança de versão no Contrato de Interfaces exigiria atualizar `ocr-worker`, `transcription-worker`, e todo Tool futuro simultaneamente.

2. **Biblioteca compartilhada (`agent_os_contract`) importada por todos os Tools para lidar com a Envelope.** Rejeitado por ora: resolveria a duplicação de código, mas ainda acoplaria todo Tool à versão do contrato — e adicionaria complexidade de empacotamento/versionamento de uma biblioteca Python interna compartilhada entre containers, que a Fase 1 não justifica ainda. Pode ser revisitado se o número de Tools crescer muito e a lógica de tradução no dispatcher ficar repetitiva demais.

3. **Cada Tool valida suas próprias permissões, sem depender do dispatcher central.** Rejeitado: violaria a decisão já tomada no ADR-0004 (permissões centralizadas no dispatcher) e duplicaria essa lógica de segurança em cada serviço, com o mesmo risco de inconsistência já identificado ali.

## Consequências

**Positivas:**
- Um Tool especialista pode ser testado, chamado e até reaproveitado **fora do Agent OS** sem nenhuma dependência do Contrato de Interfaces — por exemplo, você poderia chamar o `ocr-worker` diretamente de um script avulso (`curl http://ocr-worker:8090/extract`) para testar OCR em um documento, sem precisar montar uma Envelope inteira só para isso.
- Mudanças de versão na Message Envelope (ex: adicionar um campo novo ao contrato) afetam só `schemas.py` e o `dispatcher.py` — nenhum Tool especialista precisa ser tocado ou reimplantado.
- A curva de esforço para adicionar um novo Tool (ex: `tool.sympy` para verificação matemática, ou `tool.whisper_transcribe`) fica menor: quem escreve o serviço do Tool só precisa pensar na sua própria lógica de domínio, não em como "falar Agent OS".

**Negativas / trade-offs aceitos:**
- Toda nova capacidade exige escrever duas peças em vez de uma: o serviço do Tool em si, e o par cliente/handler no `dispatcher.py`. Isso é mais código no total do que se o Tool "só" recebesse a Envelope direto — aceito porque o código extra é simples e mecânico (sempre o mesmo padrão), enquanto o custo evitado (acoplamento de todo Tool à versão do contrato) cresce com o tempo.
- Se o número de Tools crescer muito (dezenas), o `dispatcher.py` pode ficar grande — esse risco já foi identificado e endereçado no ADR-0004 (dividir em módulos por domínio quando necessário).
