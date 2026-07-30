# Preparação de Ambiente — Agent OS v2.0 (Linux)

> Ver também: [ADR-0013](./ADR-0013-linux-pop-os-como-plataforma-de-referencia.md)
> — por que Pop!_OS foi escolhido como plataforma de referência.

Este documento cobre tudo que precisa estar pronto na máquina **antes** de
clonar o repositório do Agent OS v2.0 e subir o `docker-compose.yml`. Escrito
e testado tendo Pop!_OS como distro de referência, mas os passos valem para
qualquer distro baseada em Ubuntu/Debian — as diferenças pontuais estão
marcadas.

## Hardware de referência

| Componente | Especificação |
|---|---|
| CPU | Ryzen 7 5700G |
| GPU | RTX 5050 8GB |
| RAM | 32GB |
| SO | Pop!_OS (base Ubuntu LTS) |

O modelo LLM padrão (Qwen2.5-7B-Instruct) e os timeouts configurados no
projeto foram calibrados para esse patamar de hardware — rodando em GPU com
offload parcial para CPU quando necessário.

---

## Passo 1 — Driver NVIDIA

Se você instalou a **ISO NVIDIA do Pop!_OS**, o driver proprietário já vem
pré-instalado. Confirme:

```bash
nvidia-smi
```

Se o comando listar a GPU (RTX 5050) e a versão do driver, pule para o Passo
2. Se der `command not found`, instale o metapacote da System76:

```bash
sudo apt update
sudo apt install system76-driver-nvidia
sudo reboot
```

Em distros Ubuntu genéricas (sem o pacote da System76), o equivalente é:

```bash
sudo ubuntu-drivers autoinstall
sudo reboot
```

Depois do reboot, rode `nvidia-smi` de novo para confirmar.

---

## Passo 2 — Docker Engine

**Importante:** use o **Docker Engine** (nativo do Linux), não o Docker
Desktop — o Desktop existe para Windows/Mac e roda uma VM por baixo, o que
reintroduziria exatamente a camada de indireção que estamos eliminando ao
sair do Windows.

Forma mais simples (script oficial de conveniência, cobre Pop!_OS/Ubuntu):

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Adicione seu usuário ao grupo `docker` para não precisar de `sudo` em todo
comando:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

(`newgrp docker` aplica a mudança na sessão atual; alternativamente, faça
logout/login.)

Teste:

```bash
docker run hello-world
```

> **Nota para produção/CI:** se preferir instalar via repositório apt oficial
> da Docker em vez do script de conveniência, o codename do Pop!_OS
> (`$VERSION_CODENAME` em `/etc/os-release`) pode não ser reconhecido
> diretamente pelo repositório da Docker, já que ele é mapeado para
> codenames Ubuntu. Nesse caso, force o codename Ubuntu correspondente à
> base do seu Pop!_OS (ex: `jammy` para Pop!_OS 22.04) na configuração do
> `sources.list.d`.

---

## Passo 3 — nvidia-container-toolkit (passthrough de GPU para containers)

Isso é o que permite um container Docker enxergar a GPU do host — necessário
caso, no futuro, algum serviço do Agent OS rode inferência dentro de
container.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Teste — isso deve imprimir a mesma saída do `nvidia-smi` do host, mas de
**dentro** de um container:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Se aparecer `could not select device driver "" with capabilities: [[gpu]]`,
o runtime não foi configurado corretamente — repita
`sudo nvidia-ctk runtime configure --runtime=docker` e confirme que
`/etc/docker/daemon.json` ganhou uma seção `"nvidia"` em `"runtimes"`.

---

## Passo 4 — LM Studio para Linux

O LM Studio continua rodando **nativo no host**, fora de container — mesma
decisão da v0.1.1 (ADR-0002), agora com um motivo a mais: acesso direto ao
driver sem qualquer camada de tradução, o que não era tão direto no Windows.

1. Baixe o AppImage em https://lmstudio.ai (versão Linux).
2. Dê permissão de execução:
   ```bash
   chmod +x LM-Studio-*.AppImage
   ```
3. Se o AppImage não abrir com erro relacionado a `libfuse`, instale:
   ```bash
   sudo apt install libfuse2
   ```
4. Abra o LM Studio, baixe o modelo `Qwen2.5-7B-Instruct` (ou o que for
   definido para a v2.0), vá na aba de servidor local (**Developer** /
   **Local Server**) e inicie o servidor — porta padrão `1234`.

Confirme que o servidor responde:

```bash
curl http://localhost:1234/v1/models
```

---

## Passo 5 — Variáveis de ambiente do projeto

No `.env` do projeto (copiado de `.env.example`), o endpoint do LLM aponta
para o host via o mesmo mecanismo já usado na v0.1.1 — **sem alteração**,
porque `host.docker.internal` funciona nativamente no Docker Engine do Linux
desde a versão 20.10:

```bash
LLM_ENDPOINT=http://host.docker.internal:1234/v1
```

E no `docker-compose.yml`, cada serviço que precisa alcançar o host mantém:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

---

## Checklist final antes de subir o `docker-compose.yml`

- [ ] `nvidia-smi` mostra a RTX 5050 no host
- [ ] `docker run hello-world` funciona sem `sudo`
- [ ] `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` mostra a GPU de dentro do container
- [ ] LM Studio rodando com o servidor local ativo na porta 1234
- [ ] `curl http://localhost:1234/v1/models` responde
- [ ] `.env` configurado a partir do `.env.example`

Com os seis itens confirmados, o ambiente está pronto para `docker compose
up --build`.

---

## Troubleshooting comum

| Sintoma | Causa provável | Solução |
|---|---|---|
| `permission denied` ao rodar `docker` sem `sudo` | Usuário não está no grupo `docker`, ou sessão não recarregou | `sudo usermod -aG docker $USER` + `newgrp docker` (ou logout/login) |
| `could not select device driver "" with capabilities: [[gpu]]` | `nvidia-container-toolkit` instalado mas runtime não configurado no Docker | `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| Container não alcança o LM Studio no host | `host.docker.internal` não resolvendo | Confirmar `extra_hosts: host.docker.internal:host-gateway` no serviço e Docker Engine ≥ 20.10 (`docker version`) |
| `sqlite3.OperationalError: unable to open database file` ao subir `agent-os-api` | Bind mount de `./data/sqlite:/data` criado pelo Docker como root no primeiro `up`; o usuário não-root do container (`agentos`, uid 1000) não tem permissão de escrita | Antes do primeiro `docker compose up`: `mkdir -p data/sqlite data/vector-db` com seu próprio usuário — o bind mount preserva a propriedade da pasta do host, não a do `chown` feito dentro da imagem |
| AppImage do LM Studio não abre | Falta `libfuse2` | `sudo apt install libfuse2` |
| `nvidia-smi` não reconhece a GPU após instalar driver | Reboot pendente | `sudo reboot` |
