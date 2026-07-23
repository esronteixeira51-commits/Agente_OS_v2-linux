# ADR-0013 — Linux (Pop!_OS) como plataforma de referência do Agent OS v2.0

## Status
Aceito

## Contexto

O Agent OS v0.1.1 foi construído e testado sobre Windows. Após o processo de
análise via PCM (Protocolo de Consenso Multiagente) sobre o código-fonte real
do v0.1.1, ficou confirmado um conjunto de bugs estruturais (roteamento
incompleto no dispatcher, retry do RuntimeEngine que não reenfileira tarefas,
falhas não tratadas na calculadora, código morto) que motivaram a decisão de
não seguir com correções incrementais, e sim reconstruir o projeto do zero
como v2.0 — preservando a arquitetura já validada (contrato de Envelope,
ADR-0001 a ADR-0012, camadas `agent`/`skill`/`tool`), mas com implementação
nova e testes desde a primeira linha.

Aproveitando a reconstrução, decidiu-se também mudar a plataforma de host de
Windows para Linux.

## Decisão

**Pop!_OS** (distribuição Linux da System76, baseada em Ubuntu LTS) passa a
ser a plataforma de referência para desenvolvimento e execução do Agent OS
v2.0, rodando na mesma máquina física já usada na v0.1.1: Ryzen 7 5700G +
RTX 5050 8GB + 32GB RAM.

Docker Engine nativo do Linux, com `nvidia-container-toolkit` para
passthrough de GPU, substitui qualquer camada de compatibilidade que o
Windows exigia.

## Alternativas consideradas

- **Continuar em Windows/WSL2**: descartado. O WSL2 insere uma camada extra
  de virtualização entre o container e o driver da GPU (translation layer
  para CUDA), e essa complexidade adicional foi parte do que tornou o
  ambiente da v0.1.1 mais frágil de diagnosticar.
- **Ubuntu Server puro**: tecnicamente equivalente (Pop!_OS é baseado em
  Ubuntu), mas descartado em favor de Pop!_OS por dois motivos práticos: (1)
  é a distro que já está instalada na máquina alvo; (2) a System76 mantém
  integração de driver NVIDIA testada e simplificada (ISO com driver
  proprietário pré-instalado), reduzindo uma fonte comum de atrito em
  máquinas com GPU dedicada.

## Consequências

- `extra_hosts: host.docker.internal:host-gateway`, já usado no
  `docker-compose.yml` da v0.1.1 para o `agent-os-api` alcançar o LM Studio
  rodando no host, **continua funcionando sem alteração** — esse recurso é
  suportado nativamente pelo Docker Engine no Linux desde a versão 20.10, não
  é uma gambiarra exclusiva de Windows/Mac.
- O passthrough de GPU para containers deixa de depender de camadas de
  tradução e passa a ser nativo via `nvidia-container-toolkit`. Isso abre a
  possibilidade — a avaliar mais adiante, não decidido agora — de
  containerizar o próprio motor de LLM no futuro, algo pouco prático no
  ambiente Windows anterior.
- Todo novo setup do projeto (nova máquina, colaborador, ambiente de CI)
  passa a ter um pré-requisito documentado: ver
  `09-INFRASTRUCTURE/Preparacao_Ambiente_Linux.md`.
- Scripts e instruções específicos de Windows da v0.1.1 (ex:
  `scripts/smoke_test_planner_fixed.ps1` em PowerShell) não são portados;
  serão substituídos por equivalentes em shell/bash na v2.0.