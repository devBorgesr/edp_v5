# RELATORIO_AUDIT_V1.md — Degrau 1 do funil (NORTE.md)

## O que foi feito

Script de auditoria de retrieval, mínimo viável, para gerar o achado
concreto que sustenta a oferta comercial dos degraus 2-3 do funil:

- `audit/retrieval_audit.py` — autocontido (zero imports de `edp/`,
  stdlib apenas: `argparse`, `hashlib`, `json`, `re`, `statistics`,
  `pathlib`). Lê um export JSONL de retrieves, calcula três famílias de
  métricas (duplicação intra-query, repetição cross-query, escala de
  score) e gera relatório Markdown em PT-BR com sumário executivo,
  números por família, interpretação honesta e limitações do
  diagnóstico. Tolerante a dado malformado — nunca crasha, sempre
  pula/conta/reporta.
- `audit/export_from_edp.py` — adaptador que roda queries de um arquivo
  texto contra um store EDP real (`EDP_BASE_DIR`) e emite o JSONL acima.
  Único arquivo do pacote que importa `edp` — vive do lado EDP da
  fronteira.
- `audit/__init__.py` — vazio, só para import em teste.

## Como rodar

```bash
# a partir de um export já gerado (não precisa do EDP)
python audit/retrieval_audit.py export.jsonl -o RELATORIO.md
python audit/retrieval_audit.py export.jsonl -o RELATORIO.md --top-k 10

# gerar o export a partir de um store EDP real
export EDP_BASE_DIR=/caminho/para/uma/copia/do/store
python audit/export_from_edp.py queries.txt -o export.jsonl --top-k 10
```

## Testes

`tests/test_audit_retrieval_audit.py` — 12 testes, suíte completa do
repositório verde (124 passed, 1 deselected — marcadores
`windows_only`/`live_store` fora do escopo desta máquina):

```
python3 -m pytest
```

Cobertura: dup por hash, dup por ID, repetição cross-query (valores
calculados à mão), escala esmagada, fixture limpa sem falsos positivos,
JSONL malformado, resultado sem `text`, degradação sem `id`/sem
`score`, truncamento por `--top-k`, ponta a ponta via CLI.

## Escopo

Fora: CLI elaborado, config file, plugins, múltiplos formatos de
entrada, gráficos/HTML, LLM-as-judge, empacotamento pip, i18n,
paralelização, métricas além das três famílias do T3 (ver NORTE.md e o
prompt original do degrau 1).
