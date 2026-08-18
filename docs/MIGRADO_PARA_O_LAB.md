# Auditorias migradas para o lab (12/08/2026)

Cinco documentos que estavam na raiz deste repositório foram para
`lab_edp_novo`. Este arquivo existe para que o caminho antigo não morra em
silêncio.

## A regra

**Olhar para o EDP é trabalho de lab.** Ver, medir, cruzar dados ou prever
resultado sobre este sistema acontece em `lab_edp_novo` — que existe
justamente para o EDP não virar caixa-preta: tudo achável, rastreável,
medido. O `edp_v5` guarda o EDP; o lab guarda o que se descobriu sobre ele.

O lab já traçava essa linha no código (`bancada/` agnóstica ↔ `sujeitos/edp/`).
A partir de 12/08 ela vale para documento também — critério em
`lab_edp_novo/docs/DIVISAO.md`.

## Onde cada um foi parar

Todos em `lab_edp_novo/docs/sujeito_edp/`:

| arquivo | era |
|---|---|
| `AUDITORIA_CONSTANTES_NAO_CALIBRADAS.md` | raiz deste repo, commit `6b7a0fc` |
| `AUDITORIA_ANCORA_DE_TAREFA.md` | idem |
| `ANALISE_TOKENIZER_MEMORIA.md` | idem |
| `CRUZAMENTO_MEMORIA_INFERENCIAL_x_TOKENS.md` | idem |
| `AUDITORIA_FASE1_TOKENS.md` | raiz deste repo, commit `93cfbf5` |

O método reusável que dois deles carregavam foi **reescrito** (não copiado)
como instrumento agnóstico de sujeito, em `lab_edp_novo/docs/instrumentos/`:
`TIERS_DE_JUSTIFICATIVA.md` e `PROTOCOLO_TELEMETRIA_DE_TOKENS.md`.

## O que ficou aqui, de propósito

- **`MAPA_FUNCIONALIDADES_CLIENTE.md`** — catálogo de capacidade para
  público externo (funil do `NORTE.md §2`), não medição sobre o sujeito. O
  critério é o destinatário: quem lê é cliente, não pesquisador.
- **Código de produção e a suíte de regressão dele** — `tests/test_token_telemetry.py`
  trava contrato de código que roda em produção; fica junto do código.
- **A frente do Gap Event** (`scripts/medir_gap_score*.py`,
  `scripts/medir_alcance_wiki.py`, `scripts/lint_wiki.py`,
  `tests/test_medicao_wiki.py`, `tests/test_lint_wiki.py` e os
  pré-registros): pelo critério, é material de lab — **e não foi movida**.
  Movê-la invalidaria a lista de caminhos da Regra 1 de
  `docs/AVISO_INSTANCIA_LIMPA.md`, e decidir como registrá-la no acervo do
  lab é julgamento sobre o gabarito, que a instância que fez esta migração
  está desqualificada para fazer. Detalhe em `lab_edp_novo/docs/DIVISAO.md`,
  seção "O que NÃO foi migrado".
