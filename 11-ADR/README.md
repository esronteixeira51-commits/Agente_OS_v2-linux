# Índice de ADRs — Agent OS

`ADR-0001` não está neste repositório: o arquivo original, no dump de
commits da v0.1.1, estava em formato binário (não texto), e o
conteúdo não pôde ser recuperado na reconstrução. O contrato que ele
documentava está preservado e atualizado em
`01-ARCHITECTURE/Contrato_Interfaces.md`.

## Herdados da v0.1.1 (arquitetura preservada — ver ADR-0013)

Continuam válidos como estão; nenhum bug ou conteúdo específico de
Windows foi encontrado neles na revisão pré-reconstrução.

| ADR | Título |
|---|---|
| [ADR-0002](./ADR-0002-stack-tecnologico-escalonado-por-fase.md) | Stack tecnológico escalonado por fase |
| [ADR-0003](./ADR-0003-nomear-servicos-por-papel.md) | Nomear serviços por papel |
| [ADR-0004](./ADR-0004-dispatcher-unico-com-permissoes-centralizadas.md) | Dispatcher único com permissões centralizadas |
| [ADR-0005](./ADR-0005-tools-nao-falam-envelope.md) | Tools não falam Envelope |
| [ADR-0006](./ADR-0006-confirmacao-humana-explicita.md) | Confirmação humana explícita |
| [ADR-0007](./ADR-0007-skill-cards.md) | Skill cards |
| [ADR-0008](./ADR-0008-isolamento-por-dominio.md) | Isolamento por domínio |
| [ADR-0009](./ADR-0009-versionamento-de-skills.md) | Versionamento de skills |
| [ADR-0010](./ADR-0010-visibilidade-de-tokens.md) | Visibilidade de tokens |
| [ADR-0011](./ADR-0011-fixar-versoes-imagem-docker.md) | Fixar versões de imagem Docker |
| [ADR-0012](./ADR-0012-calculadora-deterministica-agent-tres-vias.md) | Calculadora determinística (Agent + Skill + Tool) |

## Nativos da v2.0

| ADR | Título |
|---|---|
| [ADR-0013](./ADR-0013-linux-pop-os-como-plataforma-de-referencia.md) | Linux (Pop!_OS) como plataforma de referência |
| [ADR-0014](./ADR-0014-modelo-configuravel-thinking-desligado.md) | Modelo de LLM configurável e thinking mode desligado para tool calling confiável |