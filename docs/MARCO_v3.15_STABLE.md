# MARCO v3.15 — EDP ESTÁVEL

**Data:** 2026-05-27
**Commit alvo:** `c0ef1ee` (fix: divida #10)
**Tag git:** `v3.15-stable`

---

## Propósito deste marco

Este documento registra o ponto em que o EDP atingiu **estabilidade lógica** após
a resolução das dívidas técnicas #8, #9 e #10. É o ponto seguro de retorno antes
da peça 2.4 (integração da câmara no WebSocket).

Se peças futuras introduzirem regressão grave, voltar para este estado é trivial:

```bash
git checkout v3.15-stable
```

---

## O que ESTÁ estável neste marco

### Fundação cognitiva (peça 0 inteira)

- ✅ `edp/clock.py` — relógio interno com NTP + HTTP fallback
- ✅ Schema v1 — `t_absolute`, `gap_before`, `origin`, `temporal_unreliable`
- ✅ Write atômico — `tmp + fsync + rename` com lock por path
- ✅ `edp_session_id` vitalício + `edp_session_start`
- ✅ `fresh_start.py` — script de migração idempotente

### Câmara de eco (backend completo)

- ✅ **2.0** — Infraestrutura de blocos (`blocks.py`, ~280 linhas)
- ✅ **2.1** — Roteador de refutadores (`escolher_modelos_B`, `forca_camara_detectada`)
- ✅ **2.2** — `EchoChamber` class com 7 checks, score ponderado, persistência
- ✅ **2.3** — Ativação por **auto-sinal** do modelo (não preditiva)
  - `CETICISMO_DEFAULT` refinado com HONESTIDADE
  - `detectar_auto_sinal_de_limite` com regex de 3 frases padrão + 4 variações
  - `deve_ativar_camara` reescrita: manual > auto-sinal > A=Opus > heurística legada

### Dívidas técnicas resolvidas

| # | Bug | Commit |
|---|-----|--------|
| 8 | `PermissionError [WinError 32]` em writes concorrentes Windows | `516eb5a` |
| 9 | Truncamento janela imediata em 600 chars | `cdc250d` |
| 10 | Duplicação `stream_chat` + caps em `_store_to_memory` | `c0ef1ee` |

### Validações em produção

Cenário dos 8 parágrafos sobre transformadores (2026-05-26 18:46):

- ✅ Modelo gera 8 parágrafos sobre arquitetura Transformer
- ✅ "Recapitule cada parágrafo em uma frase" → recapitulação fiel a todos os 8
- ✅ "O 7º parágrafo falou sobre quê?" → identifica exato: "Empilhamento de camadas, GPT-3 96 camadas"
- ✅ "Qual a relação entre 6/7/8?" → análise correta com transições lógicas
- ✅ Sem `PermissionError` em uso prolongado
- ✅ Sem duplicação de entries no JSON

---

## O que NÃO está neste marco

### Peças pendentes (próximas implementações)

- ⏳ **2.4** — Integração da câmara no WebSocket *(PRÓXIMA)*
- ⏳ **2.5** — Verificação humana (Camada 2)
- ⏳ **2.6** — Fechamento de bloco
- ⏳ **2.7** — Retrieval respeitando blocos
- ⏳ **2.8** — Arquitetura de janela em 4 camadas
- ⏳ **2.9** — Freios em processos secundários
- ⏳ **2.10** — Comentários em blocos antigos

### Itens fora da decomposição original

Articulados em 2026-05-27, **a serem implementados após 2.4 estabilizar**:

1. **Score composto ponderado** — temporal + semântico + classificação.
   Arquitetado mas não calibrado. Esperando dados reais da câmara em uso.

2. **Filtro de inflexão nos logs** — vetorizar só pontos críticos
   (contradições, verificações). Reduz latência e custo.

Esses dois são **otimizações** de sistemas que já funcionam, não expansão.
Vêm depois da câmara em produção porque precisam de dados reais para calibrar.

---

## Horizonte de prioridades (a partir deste marco)

```
┌─────────────────────────────────────────────────────────────┐
│ PRIORIDADE IMEDIATA                                         │
│                                                             │
│   Peça 2.4 — Câmara integrada no WebSocket                  │
│   ├── 2.4a backend (detecção mid-stream + ativação)         │
│   └── 2.4b UI (cancelamento + "reformulando" + progresso)   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ APÓS 2.4 ESTABILIZAR                                        │
│                                                             │
│   Otimizações com base em dados reais:                      │
│   ├── Score composto ponderado                              │
│   └── Filtro de inflexão nos logs                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ EXPANSÃO POSTERIOR                                          │
│                                                             │
│   Peças 2.5 → 2.10 (validação humana, fechamento de bloco,  │
│   retrieval respeitando blocos, janela em 4 camadas, etc.)  │
└─────────────────────────────────────────────────────────────┘
```

---

## Decisões já tomadas para peça 2.4

Registradas para que não precisem ser re-articuladas:

- **Streaming:** opção (II) — stream normal com cancelamento se EDP detectar
  auto-sinal mid-stream
- **Detecção:** opção (c) — detecta mid-stream mas só dispara em
  **frase-padrão completa** (uma das 3 do CETICISMO_DEFAULT)
- **UI durante câmara:** eventos `camara_progresso` ("B analisando...",
  "A avaliando...") para não parecer travada
- **Visual:** quando câmara ativa, bubble do stream é cancelado/limpo,
  mensagem "reformulando a resposta..." aparece, depois texto final
- **Decomposição:** 2.4a (backend) e 2.4b (UI) commitadas separadamente

---

## Princípio operacional

Este marco materializa o princípio sustentado durante todo o desenvolvimento:

> Sequencial puro: bloco por bloco, testado, validado em produção,
> confirmado pelo usuário, depois próximo. Sem pular. Validação qualitativa
> em peças complexas, validação binária em peças técnicas. Estabilizar
> antes de expandir.

---

## Como restaurar este marco

```bash
# Para inspecionar o estado deste marco sem mudar branch
git show v3.15-stable

# Para voltar ao estado deste marco (cria branch nova a partir dele)
git checkout -b restauracao_v3.15 v3.15-stable

# Para ver o que mudou desde este marco
git diff v3.15-stable..HEAD
```
