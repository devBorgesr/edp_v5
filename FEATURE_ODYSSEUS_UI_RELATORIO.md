# Relatório: feature/odysseus-ui
**Branch:** `feature/odysseus-ui` (criado a partir de `main`, sem merge)  
**Data de implementação:** 2026-06-26  
**Commits relevantes:** `60854a1` (feat principal), `32e32e3` (chore .gitignore)  
**Contexto:** EDP v5 — motor cognitivo local com pipeline de memória episódica,  
echo chamber multi-modelo e lineage tracker.

---

## 1. Objetivo

Construir duas features de interface no dashboard do EDP v5 inspiradas em padrões
de UX do Odysseus, expondo visualmente o que **já existia** no backend mas era
completamente ignorado pelo frontend.

Restrições explícitas durante a construção:
- Não modificar `echo_chamber.py` nem `memory.py`
- Não importar lógica de memória do Odysseus (arquitetura EDP é superior)
- Não fazer merge — entregar para revisão
- Ler e mapear antes de construir

---

## 2. Mapeamento pré-construção

### 2.1 O que já existia no backend (ignorado pelo frontend)

| Evento WS | Onde enviado | Payload original | Estado |
|-----------|-------------|-----------------|--------|
| `lineage` | `websocket.py:~1336` | `response_id, n_sources, sources[:5], model_used` | Enviado, ignorado |
| `camara_iniciada` | `websocket.py:~1060` | `camara_id, modelo_A, modelo_B, trecho_A[:200]` | Enviado, ignorado |
| `camara_fase_b_completa` | `websocket.py:~1075` | `camara_id, modelo_B, checks, latencia_ms_b, tem_reformulacao` | Enviado, ignorado |
| `camara_resultado` | `websocket.py:~1107` | `texto_final, modelo_A, modelo_B, vencedor, concordancia, camara_id` | Enviado, ignorado |

### 2.2 O que faltava em cada evento

**`lineage`**: Continha apenas `source_entries` do `LineageRecord`, que armazena
somente `{entry_id, score, source_type, timestamp}` — sem `text_preview` nem
`epistemic_status`. O `LineageTracker.build()` em `lineage.py` não enriquece os
entries com o conteúdo completo dos documentos.

**`camara_resultado`**: Não expunha o texto original de A (`texto_A`) nem os
scores detalhados da avaliação de A (`score_A`). `EchoChamber.executar()` retorna
`score_A` no dict de resultado, mas o WS handler não o repassava ao frontend.

**`ContradictionFlagger`**: Existe em `edp/runtime/contradiction_flagger.py`,
inicializado no boot do runtime, com método `scan_results(results)` que detecta
pares conflitantes por similaridade coseno (> 0.85) + assimetria de negação.
**Nunca era chamado no fluxo de retrieve do WS.**

### 2.3 Arquivos lidos antes de construir

- `edp/api/routes/websocket.py` — fluxo completo de conversa
- `edp/dashboard/static/dashboard.js` — frontend existente
- `edp/dashboard/static/dashboard.css` — design language
- `edp/echo_chamber.py` — lógica do debate (leitura apenas)
- `edp/runtime/contradiction_flagger.py` — lógica do flagger
- `edp/runtime/lineage.py` — o que LineageRecord contém
- `edp/memory.py` — campos disponíveis por retrieve()

---

## 3. Feature 1 — Memória Visível

### 3.1 Descrição funcional

Após cada resposta do assistente, um painel colapsável aparece abaixo da bolha
mostrando quais entradas de memória foram recuperadas para construir aquela resposta,
com:
- **Tipo da fonte** (badge colorido: `llm`, `user`, `summary`)
- **Status epistêmico** (badge: `verified`, `hypothesis`, `stale`)
- **Tempo relativo** ("há 3 dias", "há 2 semanas")
- **Score de relevância** (0.000 a 1.000)
- **Texto truncado** (120 caracteres, título completo no hover)
- **Badge de conflito** (âmbar ⚠) quando entradas contraditórias são detectadas

### 3.2 Mudanças no backend (`websocket.py`)

**Ponto 1 — Variáveis de estado por turno** (após linha 613):
```python
# Feature/odysseus-ui: fontes enriquecidas + conflitos
sources_ui:    list = []
conflict_count: int = 0
```

**Ponto 2 — Enriquecimento após `memory.retrieve()`** (após linha ~718):
```python
# ── Feature/odysseus-ui: fontes enriquecidas ─────────────
for r in retrieved[:5]:
    rel = _format_relative_time(r.get("timestamp"), _now) \
          if r.get("timestamp") else ""
    sources_ui.append({
        "text":        (r.get("text") or "")[:120],
        "rel_time":    rel,
        "source_type": r.get("source_type") or "unknown",
        "epistemic":   r.get("epistemic_status") or "hypothesis",
        "score":       round(float(r.get("ranking_score", 0.0)), 3),
    })
# ── Feature/odysseus-ui: detecção de conflito ────────────
try:
    from ...runtime.contradiction_flagger import get_flagger
    conflict_count = get_flagger().scan_results(retrieved)
except Exception as _ce:
    logger.debug("[WS] contradiction scan falhou: %s", _ce)
```

**Ponto 3 — Evento `lineage` enriquecido** (linha ~1336):
```python
await _safe_send(websocket, {
    "type":           "lineage",
    "response_id":    _rec.response_id,
    "n_sources":      _rec.n_sources,
    "sources":        _rec.source_entries[:5],   # campos originais (não removidos)
    "sources_ui":     sources_ui,                # ← adicionado
    "conflict_count": conflict_count,            # ← adicionado
    "model_used":     _rec.model_used,
})
```

**Decisão de design:** `_format_relative_time` já estava importada na mesma
seção do arquivo (`from ...llm_adapter import _format_relative_time`, linha ~680)
para uso no bloco de retrieve — reutilizá-la aqui foi natural e sem dependência nova.

**Decisão de design:** A detecção de conflito é **não-fatal** (try/except). O
`ContradictionFlagger.scan_results()` requer campo `embedding` nos dicts de
resultado. Se o store atual não retornar embeddings, o método retorna 0
silenciosamente. O except no WS handler garante que uma falha do flagger nunca
interrompe a resposta.

### 3.3 Mudanças no frontend — CSS

Novos seletores adicionados a `dashboard.css`:

```
.sources-panel          — container do painel colapsável
.sources-toggle         — botão de abrir/fechar (ícone ▶ girando com .open)
.conflict-badge         — badge âmbar ⚠ com contagem de conflitos
.sources-list           — lista oculta por padrão, .visible para mostrar
.source-item            — grid 4 colunas: [tipo][epistemic][texto][meta]
.source-type-badge      — base; variantes .llm, .user, .summary
.epistemic-badge        — base; variantes .verified, .hypothesis, .stale
.source-text            — truncamento por text-overflow
.source-meta            — tempo relativo + score (cinza, 10px)
```

### 3.4 Mudanças no frontend — JS

`addMsg()` agora retorna o elemento criado (antes: `void`) — necessário para
attachar o painel à bolha correta.

Funções adicionadas:
```javascript
_sourceTypeBadge(t)           // HTML do badge por tipo
_epistemicBadge(e)            // HTML do badge por status epistêmico
attachSourcesPanel(msgEl, sourcesUi, conflictCount)
// Constrói e appenda o painel colapsável ao msgEl
```

Handler adicionado no dispatcher de eventos WS:
```javascript
} else if (d.type === 'lineage') {
    const lastMsg = el('chat-box').lastElementChild;
    if (lastMsg && lastMsg.classList.contains('assistant')) {
        attachSourcesPanel(lastMsg, d.sources_ui, d.conflict_count || 0);
    }
    return;
```

---

## 4. Feature 2 — Compare ↔ Echo Chamber

### 4.1 Descrição funcional

O echo chamber (debate multi-modelo) já rodava em background — 3 eventos eram
enviados e ignorados. Esta feature dá face visual ao processo:

1. **Ao iniciar (`camara_iniciada`):** painel de loading aparece abaixo da bolha
   com "B refutando…" animado
2. **Fase B completa (`camara_fase_b_completa`):** loading atualiza para "A
   avaliando…" + chips coloridos dos 7 checks (PASS verde / FAIL vermelho)
3. **Resultado (`camara_resultado`):** painel de loading é **substituído** por
   layout side-by-side com A vs B, vencedor destacado, barra de concordância,
   e contagem de checks

### 4.2 Os 7 checks de refutação (B avalia A)

`confabulacao` | `inflacao_avaliativa` | `condescendencia` |
`projecao_sem_dado` | `perda_de_fio` | `completude_forcada` | `estruturacao_imposta`

Cada check retorna `{verdict: "PASS"|"FAIL", justificativa: string}`.

### 4.3 Mudanças no backend (`websocket.py`)

**Ponto — `camara_resultado` enriquecido** (~linha 1107):
```python
await _safe_send(websocket, {
    "type":          "camara_resultado",
    "texto_final":   texto_final,
    "texto_A":       full_text,           # ← adicionado: texto original de A
    "modelo_A":      modelo_A,
    "modelo_B":      modelo_B,
    "vencedor":      vencedor,
    "concordancia":  chamber_result.get("concordancia"),
    "score_A":       chamber_result.get("score_A"),  # ← adicionado: {score, max_score, pass_count, fail_count, fails_pesados}
    "camara_id":     camara_id,
})
```

**Insight crítico:** No momento em que `camara_resultado` é enviado (~linha 1085
do WS handler), `full_text` ainda contém o texto original de A. Ele só é
sobrescrito em `full_text = texto_final` na **linha 1096**, após o envio do
evento. Isso permitiu adicionar `texto_A: full_text` **sem modificar
`echo_chamber.py`**.

`score_A` já estava disponível em `chamber_result.get("score_A")` — o
`EchoChamber.executar()` retorna este campo, mas o handler original não o
repassava.

**`echo_chamber.py` não foi modificado.**

### 4.4 Mudanças no frontend — CSS

```
.camara-panel           — container com borda accent (roxo)
.camara-header          — cabeçalho com label + badge vencedor + modelos
.camara-loading         — estado de loading com animação pulse
.vencedor-badge         — variantes .A (ciano), .B (roxo), .ambos (verde)
.compare-columns        — grid 2 colunas para A vs B
.compare-col            — coluna individual com header + texto
.compare-col-header     — label com nome do modelo
.compare-text           — texto da resposta; .winner tem borda esquerda accent, .loser tem opacity 0.6
.check-chip             — variantes .pass (verde) e .fail (vermelho)
.camara-synthesis       — linha de rodapé com concordância + contagem
.concordancia-bar       — barra visual de concordância
.concordancia-fill      — fill proporcional ao valor de concordância
```

### 4.5 Mudanças no frontend — JS

Variáveis de estado adicionadas ao topo do IIFE:
```javascript
let _camaraActive = null;  // {camara_id, modelo_A, modelo_B, trecho_A}
let _camaraChecks = {};    // checks de camara_fase_b_completa
```

Funções adicionadas:
```javascript
_checkChips(checks)
// Gera HTML de chips PASS/FAIL com tooltip da justificativa

attachCamaraLoading(msgEl, info)
// Cria painel de loading com id="camara-<camara_id>"
// (id permite localizar e atualizar o elemento depois)

updateCamaraFaseB(camaraId, checks)
// Localiza painel por id, atualiza texto + insere chips de checks

finalizeCamaraPanel(camaraId, textoA, textoFinal, modeloA, modeloB, vencedor, concordancia, scoreA)
// Substitui innerHTML do painel com layout final side-by-side
```

Handlers adicionados no dispatcher WS (antes do handler `start`):
```javascript
} else if (d.type === 'camara_iniciada') {
    _camaraActive = d;
    _camaraChecks = {};
    const lastMsg = el('chat-box').lastElementChild;
    if (lastMsg && lastMsg.classList.contains('assistant')) {
        attachCamaraLoading(lastMsg, d);
    }
    setStage('⚡ Câmara: ' + d.modelo_B + ' refutando…');
    return;
} else if (d.type === 'camara_fase_b_completa') {
    _camaraChecks = d.checks || {};
    updateCamaraFaseB(d.camara_id, d.checks);
    setStage('⚡ Câmara: A avaliando reformulação de B…');
    return;
} else if (d.type === 'camara_resultado') {
    finalizeCamaraPanel(
        d.camara_id, d.texto_A, d.texto_final,
        d.modelo_A, d.modelo_B, d.vencedor,
        d.concordancia, d.score_A,
    );
    _camaraActive = null;
    setStage('⚡ Câmara concluída — vencedor: ' + d.vencedor);
    return;
} else if (d.type === 'start') {
    _camaraActive = null;   // reset no início de novo turno
    _camaraChecks = {};
    addMsg('assistant', '');
```

**Reset no `start`:** Se o usuário enviar nova mensagem antes do resultado da
câmara chegar (raro mas possível), o estado `_camaraActive` é limpo para não
corromper o próximo turno.

---

## 5. Limitação documentada

**Coluna B quando A vence:** Quando `vencedor === "A"`, B pode ter produzido uma
reformulação que foi avaliada e recusada por A. Esse texto (`texto_B_reformulacao`)
não está no payload de nenhum dos 3 eventos existentes sem modificar
`echo_chamber.py`. A coluna B do Compare exibe o placeholder
`"(B não reformulou ou reformulação vetada)"`.

Para expor este texto, seria necessário adicionar `texto_B_reformulacao` ao dict
retornado por `EchoChamber.executar()` e ao evento `camara_resultado`. Isso não
foi feito pela restrição explícita de não modificar `echo_chamber.py`.

---

## 6. Diff completo

### 6.1 `edp/api/routes/websocket.py` (+36 / −5 linhas)

```diff
@@ -611,6 +611,9 @@ async def ws_chat(websocket: WebSocket, session_id: str):
             lineage_retrieved: list = []
             lineage_quality:   dict = {}
+            # Feature/odysseus-ui: fontes enriquecidas + conflitos
+            sources_ui:    list = []
+            conflict_count: int = 0

@@ -715,6 +718,25 @@ async def ws_chat(websocket: WebSocket, session_id: str):
                         retrieved = memory.retrieve(message, top_k=5, min_score=0.20)
                         lineage_retrieved = retrieved
+
+                        # ── Feature/odysseus-ui: fontes enriquecidas ─────────────
+                        for r in retrieved[:5]:
+                            rel = _format_relative_time(r.get("timestamp"), _now) \
+                                  if r.get("timestamp") else ""
+                            sources_ui.append({
+                                "text":        (r.get("text") or "")[:120],
+                                "rel_time":    rel,
+                                "source_type": r.get("source_type") or "unknown",
+                                "epistemic":   r.get("epistemic_status") or "hypothesis",
+                                "score":       round(float(r.get("ranking_score", 0.0)), 3),
+                            })
+                        # ── Feature/odysseus-ui: detecção de conflito ────────────
+                        try:
+                            from ...runtime.contradiction_flagger import get_flagger
+                            conflict_count = get_flagger().scan_results(retrieved)
+                        except Exception as _ce:
+                            logger.debug("[WS] contradiction scan falhou: %s", _ce)

@@ -1085,10 +1107,12 @@ async def ws_chat(websocket: WebSocket, session_id: str):
                                                     await _safe_send(websocket, {
                                                         "type":          "camara_resultado",
                                                         "texto_final":   texto_final,
+                                                        "texto_A":       full_text,
                                                         "modelo_A":      modelo_A,
                                                         "modelo_B":      modelo_B,
                                                         "vencedor":      vencedor,
                                                         "concordancia":  chamber_result.get("concordancia"),
+                                                        "score_A":       chamber_result.get("score_A"),
                                                         "camara_id":     camara_id,
                                                     })

@@ -1312,11 +1336,13 @@ async def ws_chat(websocket: WebSocket, session_id: str):
                         await _safe_send(websocket, {
-                            "type":        "lineage",
-                            "response_id": _rec.response_id,
-                            "n_sources":   _rec.n_sources,
-                            "sources":     _rec.source_entries[:5],
-                            "model_used":  _rec.model_used,
+                            "type":           "lineage",
+                            "response_id":    _rec.response_id,
+                            "n_sources":      _rec.n_sources,
+                            "sources":        _rec.source_entries[:5],
+                            "sources_ui":     sources_ui,
+                            "conflict_count": conflict_count,
+                            "model_used":     _rec.model_used,
                         })
```

### 6.2 `edp/dashboard/static/dashboard.css` (+233 linhas)

233 linhas de CSS adicionadas ao final do arquivo cobrindo todos os seletores
documentados nas seções 3.3 e 4.4 acima.

### 6.3 `edp/dashboard/static/dashboard.js` (+189 / −1 linhas)

1 linha modificada: `addMsg()` passou a retornar `msg` (antes: sem return).  
189 linhas adicionadas: variáveis de estado, 6 funções helper, 5 handlers WS.

### 6.4 `.gitignore` (+1 linha)

```diff
 # Dados do EDP (ficam fora do repo, mas garantia)
 edp_data/
+data/
```

Motivo: branch criada a partir de `main`; o fix `data/` existia em
`fase4-dead-branch-cleanup` mas nunca foi mergeado. Sem este entry, o diretório
`data/pareto/events.jsonl` aparecia como untracked a cada execução do runtime.

---

## 7. Arquitetura de eventos WS — sequência por turno

```
usuário envia mensagem
  │
  ├─ [WS→client] "start"
  │     → _camaraActive = null, _camaraChecks = {} (reset JS)
  │
  ├─ pipeline roda (intent, contexto, memória)
  │
  ├─ memory.retrieve() → retrieved[]
  │     → sources_ui[] construído aqui
  │     → ContradictionFlagger.scan_results(retrieved) → conflict_count
  │
  ├─ LLM streaming (modelo A)
  │     → [WS→client] "token" × N  (texto sendo digitado)
  │     → full_text acumula
  │
  ├─ (se echo chamber ativo)
  │     ├─ [WS→client] "camara_iniciada"
  │     │     → JS: attachCamaraLoading() — painel loading aparece
  │     │
  │     ├─ modelo B refuta e avalia 7 checks
  │     │
  │     ├─ [WS→client] "camara_fase_b_completa"
  │     │     → JS: updateCamaraFaseB() — chips PASS/FAIL
  │     │
  │     ├─ decisão: A aceita ou B vence
  │     │
  │     └─ [WS→client] "camara_resultado"
  │           texto_final | texto_A (=full_text NESTE momento) | score_A | vencedor
  │           → JS: finalizeCamaraPanel() — layout side-by-side
  │
  ├─ (se B venceu) full_text = texto_final  ← DEPOIS do evento
  │
  ├─ lineage calculado e persistido
  │
  └─ [WS→client] "lineage"
        sources_ui[] | conflict_count
        → JS: attachSourcesPanel() — painel colapsável de fontes
```

---

## 8. Decisões de design

| Decisão | Alternativa rejeitada | Motivo |
|---------|----------------------|--------|
| `texto_A = full_text` no momento do send | Modificar `echo_chamber.py` para retornar `texto_B` | Restrição explícita; insight de timing elimina necessidade |
| Contradiction flagger em try/except | Falhar silenciosamente ou ignorar | Flagger requer `embedding`; stores sem embedding retornam 0 por design — excepção capturada para robustez |
| `id="camara-<camara_id>"` no DOM | Variável JS global com referência | Permite atualizações progressivas sem estado frágil |
| `addMsg()` retorna elemento | Variável global com último elemento | Minimal change, evita estado mutável desnecessário |
| `sources_ui` separado de `LineageRecord.source_entries` | Enriquecer `LineageTracker.build()` | `lineage.py` não foi modificado; enriquecimento acontece no WS handler onde `retrieved[]` está disponível |
| `concordancia-fill` em px fixo (width: concord × 0.8) | % relativo ao container | Bar simples sem container sizing — funciona para valores 0-100 |

---

## 9. Perguntas abertas para o revisor

1. **`texto_B_reformulacao`**: Vale adicionar este campo ao retorno de
   `EchoChamber.executar()` para preencher a coluna B quando A vence?
   Impacto: 2-3 linhas em `echo_chamber.py` e 1 linha no WS handler.

2. **Contradiction flagger sem embeddings**: Se o store atual não retorna
   `embedding` nos dicts de `retrieved`, `conflict_count` será sempre 0.
   Devemos garantir que `memory.retrieve()` inclua embeddings, ou adicionar
   fallback de similaridade por texto?

3. **`sources_ui` com 120 chars**: O truncamento de `text[:120]` é suficiente
   para identificação visual? Considerar se 80 ou 200 seriam melhores.

4. **`concordancia-fill` width**: O cálculo atual (`concordancia × 0.8`px) é
   arbitrário. Considerar tornar relativo ao width do container pai.

5. **Integração futura**: Os 3 eventos de câmara (`camara_iniciada`,
   `camara_fase_b_completa`, `camara_resultado`) existiam antes desta feature.
   Outros consumidores desses eventos (logs, analytics) continuam funcionando
   pois apenas campos novos foram adicionados — nenhum campo existente foi
   removido ou renomeado.

---

## 10. Estado do branch

```
branch: feature/odysseus-ui
base:   main
HEAD:   32e32e3  chore: ignora data/ (telemetria de runtime)
        60854a1  feat(ui): memória visível + compare echo chamber (feature/odysseus-ui)
```

Arquivos modificados em relação a `main`:
- `edp/api/routes/websocket.py` (+36 / −5)
- `edp/dashboard/static/dashboard.css` (+233 / −0)
- `edp/dashboard/static/dashboard.js` (+189 / −1)
- `.gitignore` (+1 / −0)

**Nenhum arquivo de lógica foi modificado** (`echo_chamber.py`, `memory.py`,
`contradiction_flagger.py`, `lineage.py` — todos intocados).
