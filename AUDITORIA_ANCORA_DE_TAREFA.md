# Auditoria — mecanismo de Âncora de Tarefa (peças 2.6a–2.6f)

**Data:** 2026-08-12. **Escopo:** todo o código que implementa, aciona ou lê
o estado `_task_anchor` do `EDPRuntime`, mais os pontos de acoplamento em
`websocket.py`, `model_router.py` e `config.py`. Método: grep por `ancora`
(com/sem acento) em todo o repo + leitura completa de cada trecho no
contexto, cruzado com `docs/MARCOS_EPISTEMICOS.md` (registro histórico das
peças) e o teste dedicado (`tests/test_anchor_compact.py`).

---

## 0. Desambiguação — a palavra "âncora" nomeia TRÊS mecanismos diferentes

Isto importa porque um grep ingênuo por "anchor"/"ancora" mistura os três.
Esta auditoria é sobre o **primeiro**; os outros dois aparecem só para
marcar a fronteira.

| nome | o que é | onde vive | está nesta auditoria? |
|---|---|---|---|
| **Âncora de Tarefa** (peça 2.6c–2.6f) | estado de progresso de uma tarefa multi-seção, injetado como Camada 0.5 do prompt | `llm_adapter.py::EDPRuntime._task_anchor` e métodos correlatos | **sim — é o objeto desta auditoria** |
| Âncora Temporal | bloco `[ÂNCORA TEMPORAL]` com data/hora atual, injetado como Camada 0 (antes da 0.5) | `llm_adapter.py:2080-2098` | não — mecanismo adjacente, mencionado só para mostrar onde a Âncora de Tarefa se encaixa na pilha |
| Âncora epistêmica (`is_epistemic_anchor`) | flag em ENTRADA DE MEMÓRIA que ganha boost 1.20 no ranking de retrieval quando o texto admite um limite de conhecimento | `edp/memory_classifier.py`, `edp/memory/store.py:576` | não — já coberta em `AUDITORIA_CONSTANTES_NAO_CALIBRADAS.md §2.3` como Tier B |

Ruído adicional descartado por leitura de contexto: `TAGS_ANCORA` em
`docs/preregistro_estabilizacao_3_frentes.md` são tags git (`v3.15-stable`
etc.), sem relação; "ancorada em `file:line`" em `DIAGNOSTICO_SESSION_SUMMARY.md`
é o verbo comum, não o mecanismo; `ancora_envenenada` em
`edp/lab/window_formats.py`/`run_once.py` testa a Âncora **Temporal**
(troca a data para ver se o modelo confabula), não a de Tarefa.

---

## 1. O mecanismo, peça por peça

| peça | data | o que resolveu | onde |
|---|---|---|---|
| 2.6a | 30/05 manhã | modo bimodal `cognitive`/`sprint` — pré-requisito de sectioned | `llm_adapter.py:919-1034` |
| 2.6b | 30/05 tarde | entrega por seção (`/sectioned`), formato contratado `## Seção N/M — Título` | `llm_adapter.py:1036-1095` |
| 2.6c | 30/05 tarde | **a âncora em si**: Camada 0.5, bloco `[ÂNCORA DE TAREFA EM CURSO]` | `llm_adapter.py:1097-1301`, injeção em `2102-2125` |
| 2.6d M2 | 30/05 | roteador de modelo preserva tier em mensagem curta de continuação | `model_router.py:180-205` |
| 2.6e M1 | 30/05 | captura de decisões técnicas por seção (`<!-- decisions: {...} -->`) e bloco consolidado | `llm_adapter.py:1189-1226`, `1346-1382` |
| 2.6e + Commit 2 | 30/05–31/05 | detecção robusta de continuação (4 camadas, incl. referência contextual "seção 3") | `websocket.py:172-229` |
| 2.6f | 07/08/2026 | `EDP_ANCHOR_COMPACT` — comprime a listagem por-seção, preserva cadeia de mudança no consolidado | `config.py:257-275`, `llm_adapter.py:1313-1383` |

Todas as peças têm data e motivo registrados — nisso o mecanismo segue a
disciplina do resto do projeto. O que esta auditoria examina é o que
sobrou sem citação dentro dessa disciplina.

---

## 2. Ciclo de vida completo

```
usuário liga sprint + sectioned
        │
        ▼
mensagem chega ao WS ──► _detect_continuation() (4 camadas, websocket.py:172)
        │                         │
        │                    é continuação?
        │                    ┌────┴────┐
        │                   sim        não
        │                    │          │
        │           (não reinicia)   runtime.start_task(message)
        │                              cria _task_anchor {challenge, sections_delivered=[],
        │                              expected_total=None, started_at}
        ▼
runtime.format_task_anchor() formata bloco, injetado como Camada 0.5
        │
        ▼
LLM responde "## Seção N/M — Título" + <!-- decisions: {...} -->
        │
        ▼
runtime.register_section_delivered(resposta) — parser regex determinístico
        │  atualiza sections_delivered, extrai decisions, atualiza consolidated
        ▼
n_entregues >= total?  ──sim──► emit_task_completed (Pareto) + _task_anchor=None
        │
       não
        ▼
próximo turno repete a partir de format_task_anchor()
```

Comandos manuais: `/task status` (lê sem alterar), `/task clear` (força
`clear_task()`), `/sectioned off` (limpa a âncora como efeito colateral,
`llm_adapter.py:1087-1090`).

---

## 3. Achado principal — o defeito auto-declarado continua sem teto, e ainda sem teste que o exercite

`docs/MARCOS_EPISTEMICOS.md:242-247` (nota de 07/08/2026, escrita pelo
próprio projeto) já registra:

> "o bloco `consolidated` não tem teto e cresce com o número de seções,
> enquanto todos os outros campos da âncora têm. **Não medido.**"

Fui ao código (`llm_adapter.py:1346-1382`) confirmar se isso segue
verdadeiro após a peça 2.6f, que foi desenhada justamente para essa
família de problema. **Continua verdadeiro, e o motivo é específico:**

- O laço que **exibe** decisões por seção corta em 6 chaves:
  `list(s["decisions"].items())[:6]` (`llm_adapter.py:1341`).
- O laço que **constrói** `consolidated`, três linhas abaixo, itera
  `s["decisions"].items()` **sem o mesmo corte** (`llm_adapter.py:1352`) —
  se uma seção trouxer 15 chaves de decisão (nada no parser impede isso;
  o corte de 6 é só uma convenção do system prompt, não uma validação),
  todas as 15 entram no consolidado.
- `consolidated[k]["changes"]` (a cadeia de re-decisão que a 2.6f
  introduziu) não tem cap de tamanho — cada re-decisão da mesma chave em
  seções sucessivas adiciona um item, para sempre, dentro do bloco Camada
  0.5 que o próprio código descreve como "**o único bloco sem teto**"
  (comentário em `config.py:271`, ecoando a nota do marco).
- Comparar com os campos que TÊM teto no mesmo objeto: `challenge` corta
  em 2000 chars na criação e 800 no render (`:1116`, `:1321`); `title`
  corta em 120 (`:1174`); `summary` corta em 200 (`:1187`); cada valor de
  decisão corta em 120–200 chars (`:1342`, `:1373`). O padrão do resto do
  objeto é "todo campo tem corte"; `consolidated` quebra esse padrão.

**A peça 2.6f não fechou o defeito, mudou o que ele mede.** Antes da 2.6f,
o custo não-tetado crescia com `n_seções × n_decisões_por_seção`. Depois,
com `EDP_ANCHOR_COMPACT=1`, a listagem por-seção (a maior parte do custo,
79% medido) some, mas o `consolidated` — que também não tem teto — passa a
ser uma fração maior do que sobra, e ganha uma segunda dimensão de
crescimento (`changes` por chave) que não existia antes.

### 3.1 — O teste dedicado não exercita este caminho

`tests/test_anchor_compact.py` é rigoroso no que se propõe (flag-off
byte-idêntico, redução ≥60%, cabe no cap do turno-1) — mas todo teste usa
`n_dec=6` fixo (o próprio limite de exibição) e no máximo **uma**
re-decisão (`muda_na_secao=4`, uma vez). Não existe teste com:
- uma seção com mais de 6 chaves de decisão (caminho que escapa do corte
  de exibição e alimenta `consolidated` sem filtro);
- uma chave re-decidida em várias seções sucessivas (crescimento real de
  `changes`);
- `EDP_ANCHOR_COMPACT=1` numa tarefa de 10+ seções com múltiplas
  re-decisões — o cenário exato que tornaria o defeito visível em
  caracteres.

Ou seja: a suíte prova que a peça 2.6f faz o que foi desenhada para fazer
(comprimir o caso comum), não que o defeito que motivou a preocupação do
marco epistêmico foi contido.

---

## 4. Heurísticas com corte numérico sem medição citada

Mesmo padrão da auditoria de constantes anterior, aqui concentrado no
detector de continuação (`websocket.py::_detect_continuation`,
`:172-229`):

| corte | valor | papel | evidência de origem |
|---|---|---|---|
| tamanho "curto" (camadas 1-3) | ≤ 5 palavras | só tenta match exato/regex/Levenshtein abaixo disso | nenhuma — "5" é só o número escolhido |
| tamanho "curto" (heurística de fallback, `websocket.py:582`) | < 5 palavras | **duplica** o corte acima, num segundo lugar, como rede de segurança se `_detect_continuation` falhar | nenhuma citação cruzada com a constante da função — mesmo número, definido duas vezes, mesmo padrão do `SESSION_GAP_THRESHOLD_SEC` já achado na auditoria de constantes |
| tamanho "referência contextual" (camada 4) | ≤ 20 palavras | permite frases como "gera a seção 3?" mesmo com "?" | comentário explica a INTENÇÃO ("provável desafio" acima disso), não por que 20 e não 15 ou 25 |
| guarda absoluta | > 60 palavras | nunca é continuação | idem — sem medição citada |
| distância de Levenshtein por palavra-alvo | 0, 1 ou 2, por palavra (`_CONTINUATION_BASE`) | tolerância a typo | plausível à mão (palavras de 3 letras como "vai"/"ok" ganham tolerância 0, mais longas ganham 1-2) mas nenhum dos números tem teste que compare contra alternativa |

Nenhum destes é grave isoladamente — são heurísticas de UX, não thresholds
epistêmicos como os da auditoria anterior. Listados porque o padrão
"número redondo sem citação" se repete até em código de baixo risco, e a
duplicação de "5 palavras" em dois lugares independentes é exatamente o
tipo de achado que a auditoria de constantes já sinalizou como risco de
deriva silenciosa.

---

## 5. Onde o mecanismo TEM evidência — para não desequilibrar o quadro

- **`route_model()` preserva o tier do modelo durante tarefa em curso**
  (`model_router.py:180-205`) tem motivação empírica citada: rebaixamento
  Sonnet→Haiku no meio de uma tarefa de 10 seções causou queda medida por
  avaliador externo (5.5/10 em coesão de tecnologia, 30/05/2026). É Tier B
  na escala da auditoria de constantes — um incidente, não uma varredura —
  mas é evidência real, não invenção.
- **Os eventos de telemetria são consumidos, não descartados.** Ao
  contrário dos 4 sinais mortos já catalogados no `README.md §4`
  (`cognitive_decisions`, `contradiction_flagger`, `reflection.reweights`,
  `RETRIEVAL_BACKEND`), `task_started`/`task_completed`
  (`runtime/pareto_store.py:454-495`) alimentam
  `bayes_calibrator.py` (taxa de conclusão `P(task_completed|task_started)`,
  se troca de modo precede início de tarefa) e `gauss_calibrator.py`
  (detecção de outlier em `expected_total`/`n_secoes`/`duration_sec`).
  Não verifiquei nesta auditoria se o output desses calibradores, por sua
  vez, chega a alguma view — isso é uma pergunta separada sobre
  `health_index.py`/CHI, fora do escopo desta auditoria.
- **`EDP_ANCHOR_COMPACT` em si é Tier D** (medido): 9.100 de 11.486 chars
  (79%) creditados à linha por-seção, medição datada e repetida no
  docstring do teste. Redução ≥60% é travada por teste
  (`test_flag_on_reduz_pelo_menos_60_por_cento`). O que fica sem medição
  não é a flag — é o crescimento do que sobra depois dela, tratado em §3.

---

## 6. Veredito

O mecanismo de Âncora de Tarefa é a parte do EDP com a documentação
histórica mais completa (cada peça tem data, motivo e caso real
motivador em `docs/MARCOS_EPISTEMICOS.md`) — e mesmo assim carrega um
defeito estrutural que o próprio projeto já nomeou e ainda não fechou: o
bloco que a peça 2.6c descreveu como "tratado como verdade absoluta" pelo
modelo cresce sem limite justamente na dimensão (`consolidated`/`changes`)
que a peça mais recente (2.6f) adicionou. Não é um achado escondido — é
um achado **anotado e não seguido**, o que é diferente e, para fins de
priorização, mais fácil de fechar: a especificação do que falta já existe
("todos os outros campos têm teto"), falta só aplicar o mesmo corte a
`consolidated` e escrever o teste de 3.1 que prove isso.

Não fiz a correção — é auditoria, não patch; prioridade é decisão do
Daniel, igual à auditoria de constantes anterior.
