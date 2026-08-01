# Pré-processador de Ingestão — Agent OS v2.0

Pipeline **determinístico** (sem LLM) que transforma um documento
(`.txt`, `.md`, `.pdf` com texto embutido) em chunks indexáveis na
base de conhecimento, e uma Envelope pronta para `POST /v1/dispatch`.

Ver o planejamento completo em
[`Ingestao_Base_Conhecimento_e_Notas.md`](../../01-ARCHITECTURE/Ingestao_Base_Conhecimento_e_Notas.md)
para o raciocínio por trás de cada decisão de design.

## Por que roda fora do Docker, no host

Sem competir por VRAM com o LM Studio, e testável offline, sem
precisar de nenhum container de pé.

## Modos

- **`passthrough`** — para `.md` que você já organizou. Preserva o
  texto exatamente como está; só fatia em chunks e monta o payload.
- **`clean`** — para `.txt`/PDF com ruído leve. Aplica limpeza
  determinística (remove número de página, rejunta hífen de quebra
  de linha) e heurísticas de Markdown (detecta capítulos, seções
  numeradas, títulos curtos).

Nenhum dos dois modos usa LLM.

## Uso

```bash
pip install -r requirements.txt

python -m app.cli --mode passthrough \
  -i artigo.md -s meu-artigo -d matematica -o ./out

# gera: ./out/markdown/meu-artigo.md
#       ./out/chunks/meu-artigo_chunk0000.md, 0001.md, ...
#       ./out/chromadb_payload.json
#       ./out/manifest.json
```

Publicar na base vetorial:

```bash
python -m app.emit_envelope -p ./out/chromadb_payload.json -d matematica > envelope.json

curl -s -X POST http://localhost:8080/v1/dispatch \
  -H 'Content-Type: application/json' -d @envelope.json
```

Confirmar que indexou, buscando de volta:

```bash
curl -s -X POST http://localhost:8080/v1/dispatch \
  -H 'Content-Type: application/json' \
  -d '{
    "trace_id": "teste-busca-1",
    "layer_from": "runtime", "layer_to": "skill",
    "target_id": "skill.rag_search",
    "payload": {"query": "termo que deveria estar no seu documento"},
    "context": {"domain": "matematica"},
    "permissions": {"level": "read_only"}
  }'
```

## Rodando os testes

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

64 testes — todos deterministicos, sem precisar de rede, Docker, LM
Studio ou banco de dados.

## Domínios válidos

`matematica`, `courier`, `eletronica` — mesmo enum fechado do
`agent-os-api` (ADR-0008). Um domínio fora dessa lista é rejeitado
tanto aqui quanto no dispatcher.

## Parâmetros de chunk

Calibrados para modelos 7B/8B no hardware de referência — ajustáveis
via `--chunk-size` / `--chunk-overlap` se precisar:

| Parâmetro | Padrão |
|---|---|
| `chunk_size_chars` | 2800 (~700 tokens) |
| `chunk_overlap_chars` | 350 (~12%) |