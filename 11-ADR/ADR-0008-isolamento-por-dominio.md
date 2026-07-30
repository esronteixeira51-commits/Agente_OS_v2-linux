# ADR-0008 — Isolamento por Domínio como Enum Fechado

Status: Aceito
Data: 2026-07-05
Origem: RFC-0001, item R-4
Documento técnico relacionado: `services/agent-os-api/app/schemas.py`, `01-ARCHITECTURE/Contrato_Interfaces.md`

---

## Contexto

O Agent OS opera três domínios de conhecimento distintos e sensíveis entre si (matemática histórica, courier, loja de eletrônicos). Sem isolamento explícito, existe risco real de contaminação cruzada na busca vetorial — por exemplo, uma consulta sobre "margem de lucro" trazendo de volta contexto de matemática histórica por coincidência de similaridade semântica, ou dado de uma empresa vazando numa resposta sobre a outra.

A pergunta em aberto deixada no RFC-0001 era se o campo de domínio deveria ser uma string livre (flexível, mas sujeita a erro de digitação silencioso) ou um enum fechado (mais rígido, mas seguro por construção).

## Decisão

Introduzir `Domain = Literal["matematica", "courier", "eletronica"]` em `schemas.py`, como **enum fechado**, não string livre. Qualquer operação que toque recursos com escopo de domínio (a começar por `skill.rag_search`, quando implementada) exige esse campo dentro de `context`, validado pela camada receptora — seguindo o mesmo princípio já estabelecido no Contrato de Interfaces (seção 7) de nunca confiar na declaração do chamador sem checagem.

Adicionar um domínio novo à lista (ex: uma terceira frente de negócio) é, por definição, uma decisão arquitetural — e portanto exige um ADR próprio, não uma edição livre de configuração.

## Alternativas consideradas

1. **String livre para `domain`.** Rejeitado: um erro de digitação (`"matematica"` vs `"matemática"` vs `"math"`) faria o isolamento falhar silenciosamente — exatamente o tipo de bug sutil e perigoso de detectar, porque não gera erro nenhum, só um resultado de busca vetorial levemente errado que pode passar despercebido.
2. **Enum aberto por configuração (arquivo `.yaml` editável sem exigir ADR).** Rejeitado: reduziria a fricção de adicionar domínio novo, mas essa fricção é proposital — trata-se de uma decisão que afeta isolamento de dados sensíveis entre dois negócios reais, e merece o mesmo nível de deliberação que qualquer outra decisão arquitetural registrada neste projeto.

## Consequências

**Positivas:**
- Erros de domínio inválido são pegos imediatamente como `INVALID_INPUT`, nunca como vazamento silencioso de contexto entre negócios.
- Adicionar domínio novo fica documentado por design (via ADR), preservando o Princípio 4 do Manifesto ("todo conhecimento possui origem") aplicado também à própria estrutura de isolamento.

**Negativas / trade-offs aceitos:**
- Adicionar um domínio novo exige um passo a mais (ADR + mudança de código) do que simplesmente digitar uma string nova — aceito conscientemente, dado o risco que a alternativa mais flexível introduziria.
