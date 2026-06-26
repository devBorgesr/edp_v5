# Contrato de API — EDP v5
**Branch:** `feature/front-novo`  
**Data:** 2026-06-26  
**Finalidade:** Documentação completa da superfície de API do backend EDP v5
para consumo pelo novo frontend. Nenhum arquivo Python foi modificado para
produzir este documento.

---

## 1. Visão Geral

O EDP v5 expõe duas superfícies de API:

| Superfície | Endpoint Base | Autenticação |
|-----------|--------------|--------------|
| WebSocket | `ws://host:8000/ws/chat/{session_id}` | Nenhuma (CORS `*`) |
| REST / HTTP | `http://host:8000/` | Nenhuma (CORS `*`) |

O servidor tem `CORSMiddleware` com `allow_origins=["*"]`, portanto o novo
frontend pode chamar o backend diretamente via proxy no dev server (sem
necessidade de configuração adicional no backend).

**Sessão padrão:** todos os endpoints usam `session_id="default"` quando não
especificado. O frontend novo pode usar o mesmo `"default"` por enquanto.

---

## 2. Protocolo WebSocket — Contrato Completo

### 2.1 Endpoint

```
WS  ws://host:8000/ws/chat/{session_id}
```

O servidor aceita a conexão imediatamente (`await websocket.accept()`).
Um heartbeat é enviado em task separada enquanto a conexão estiver viva.

### 2.2 Mensagem do Cliente → Servidor

```json
{ "message": "texto da mensagem do usuário" }
```

- Campo `message` (string): obrigatório, não-vazio após `.strip()`
- Mensagem vazia → servidor responde com `{type:"error", error:"mensagem vazia"}`

### 2.3 Sequência Completa de Mensagens Servidor → Cliente

As mensagens são enviadas **em ordem**. Nem todas ocorrem em todo turno —
veja condições em cada item.

```
────────────────── INÍCIO DO TURNO ──────────────────

[1] start                   (SEMPRE)
{
  "type":       "start",
  "session_id": "default",
  "stage":      "pipeline" | "command"
}
Indica que o servidor começou a processar. O frontend deve:
- Criar nova bolha de assistente (vazia, modo streaming)
- Desabilitar o input
- stage="command" quando é um /mode, /sectioned ou /task (resposta rápida)

[2] quality                 (OPCIONAL — se feature flag ativa e pipeline ok)
{
  "type":       "quality",
  "score":      0.0-1.0,
  "verdict":    "excellent"|"good"|"ok"|"poor"|"degraded",
  "components": { ... },
  "source":     "pipeline_aggregate"
}
Score de qualidade cognitiva do pipeline (compressão + contexto).

[3] pipeline_done           (SEMPRE)
{
  "type":            "pipeline_done",
  "compression_pct": 23.5,      // % de compressão de contexto
  "memory_hits":     3,          // entradas de memória usadas
  "retrieved":       ["texto1", "texto2"],  // 3 primeiros blocos (preview)
  "pipeline_ok":     true
}

[4] router_decision         (OPCIONAL — só com modelo cloud Anthropic)
{
  "type":   "router_decision",
  "model":  "claude-haiku-4-5",   // modelo escolhido para este turno
  "tier":   "haiku"|"sonnet"|"opus",
  "reason": "mensagem curta, baixa complexidade",
  "cost":   0.003,
  "badge":  "⚡ haiku"
}

[5] llm_start               (OPCIONAL — só se LLM conectado)
{
  "type":  "llm_start",
  "model": "claude-haiku-4-5"
}

[6] warn                    (OPCIONAL — se RAM crítica ou fila cheia)
{
  "type":  "warn",
  "error": "Sistema sob pressão de memória..."
}

[7] chunk                   (MÚLTIPLOS — uma por token/fragmento)
{
  "type": "chunk",
  "text": "fragmento de texto"
}
O frontend deve acumular e exibir em streaming.

────────── SE echo chamber ATIVOU (opcional) ──────────

[8] camara_iniciada
{
  "type":      "camara_iniciada",
  "camara_id": "uuid",
  "modelo_A":  "claude-sonnet-4-6",
  "modelo_B":  "claude-opus-4-8",
  "trecho_A":  "primeiros 200 chars da resposta de A"
}
Indica que o debate multi-modelo começou. B está avaliando A.

[9] camara_fase_b_completa
{
  "type":             "camara_fase_b_completa",
  "camara_id":        "uuid",
  "modelo_B":         "claude-opus-4-8",
  "checks":           {
    "confabulacao":          {"verdict": "PASS", "justificativa": "..."},
    "inflacao_avaliativa":   {"verdict": "FAIL", "justificativa": "..."},
    "condescendencia":       {"verdict": "PASS", "justificativa": "..."},
    "projecao_sem_dado":     {"verdict": "PASS", "justificativa": "..."},
    "perda_de_fio":          {"verdict": "PASS", "justificativa": "..."},
    "completude_forcada":    {"verdict": "FAIL", "justificativa": "..."},
    "estruturacao_imposta":  {"verdict": "PASS", "justificativa": "..."}
  },
  "latencia_ms_b":    1230,
  "tem_reformulacao": true
}

[10] camara_resultado
{
  "type":        "camara_resultado",
  "texto_final": "texto final (de A ou B dependendo do vencedor)",
  "modelo_A":    "claude-sonnet-4-6",
  "modelo_B":    "claude-opus-4-8",
  "vencedor":    "A" | "B" | "ambos_similar",
  "concordancia": 82,    // int, 0-100%
  "camara_id":   "uuid"
}
NOTA: texto_final é o texto que substitui a resposta. Se vencedor="B",
texto_final é a reformulação de B. Se vencedor="A", é o original de A.

────────────────── FIM DO TURNO ──────────────────

[11] lineage                (OPCIONAL — se lineage habilitado e llm_used=true)
{
  "type":        "lineage",
  "response_id": "uuid",
  "n_sources":   3,
  "sources":     [
    {
      "entry_id":    "uuid",
      "score":       0.742,
      "source_type": "llm_response" | "user_input" | "session_summary",
      "timestamp":   1718000000.0
    }
  ],
  "model_used":  "claude-haiku-4-5"
}
Proveniência: quais memórias alimentaram esta resposta.

[12] done                   (SEMPRE — mesmo em erro)
{
  "type":            "done",
  "text":            "texto completo da resposta",
  "llm_used":        true,
  "memory_hits":     3,
  "compression_pct": 23.5,
  "metrics":         { ... }
}

[13] error                  (OPCIONAL — em exceções)
{
  "type":  "error",
  "error": "ExceptionType: mensagem"
}

[14] heartbeat              (PERIÓDICO — a cada ~15s)
{
  "type": "heartbeat"
}
O frontend deve ignorar este evento (não exibir, não processar).
```

### 2.4 Comandos Especiais (prefixo `/`)

Enviados como mensagem normal `{message: "/mode sprint"}`. O servidor
responde no mesmo formato `start → chunk → done` mas sem LLM.

| Comando | Efeito |
|---------|--------|
| `/mode cognitive` | Troca para modo cognitivo |
| `/mode sprint` | Troca para modo sprint |
| `/mode status` | Retorna modo atual |
| `/sectioned [on\|off\|status]` | Ativa/desativa output seccionado |
| `/task [status\|clear]` | Gerencia âncora de tarefa em curso |

### 2.5 Fluxo de Reconexão

O backend fecha a conexão em caso de erro fatal. O frontend deve:
1. Detectar `WebSocketDisconnect` / `close`
2. Aguardar backoff exponencial (1s → 2s → 4s → 8s)
3. Reconectar ao mesmo `session_id`
4. O estado de memória é persistido em disco — não se perde na reconexão

---

## 3. Endpoints REST

### 3.1 Health

**`GET /health`**
```json
{
  "status":    "ok",
  "version":   "3.3.0",
  "timestamp": 1718000000.0,
  "sessions":  ["default"],
  "metrics":   { ... }
}
```

**`GET /health/registry`**  
Diagnóstico interno do registry de sessões. Útil para debug.

---

### 3.2 Dashboard State (endpoint consolidado)

**`GET /dashboard/state?session_id=default`**  
*Substitui 4 chamadas separadas em 1. Use este para o painel de status.*

```json
{
  "timestamp":   1718000000.0,
  "session_id":  "default",
  "runtime_state": {
    "state":      "READY" | "WARMING" | "DEGRADED" | "COLD" | "SHUTDOWN",
    "components": { "embeddings": true, "default_session": true },
    "error":      null
  },
  "pressure": {
    "level":        "ok" | "elevated" | "critical" | "unknown",
    "available_gb": 4.2,
    "used_pct":     62.3
  },
  "queue": {
    "active":     1,
    "waiting":    0,
    "completed":  42
  },
  "retrieval_quality": {
    "avg_score":   0.65,
    "hit_rate":    0.80
  },
  "contradictions": {
    "stats":  { "total": 3, "unreviewed": 2 },
    "recent": [ ... ]
  },
  "health": {
    "status":   "ok" | "degraded",
    "version":  "3.5.0",
    "sessions": ["default"]
  },
  "system_metrics": { ... },
  "memory": {
    "episodic": 42,
    "semantic":  8,
    "total":    50
  },
  "llm_metrics": {
    "turns":           12,
    "avg_latency_ms":  1200
  }
}
```

---

### 3.3 LLM Connection

**`POST /connect`**
```json
// Request
{
  "provider":   "anthropic" | "ollama" | "openai" | "lm_studio",
  "model":      "claude-haiku-4-5",
  "base_url":   "http://localhost:11434",
  "api_key":    "",
  "session_id": "default"
}

// Response
{
  "connected": true,
  "provider":  "anthropic",
  "model":     "claude-haiku-4-5",
  "models":    ["claude-haiku-4-5", "claude-sonnet-4-6", ...]
}
```

**`GET /providers`**
```json
{
  "available":           ["anthropic", "ollama"],
  "anthropic_models":    ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5", ...],
  "has_anthropic_key_in_env": true
}
```

**`POST /providers/validate`**
```json
// Request
{ "provider": "anthropic", "api_key": "sk-...", "model": "claude-haiku-4-5" }

// Response
{ "provider": "anthropic", "model": "claude-haiku-4-5", "valid": true }
```

---

### 3.4 Memória — CRUD e Análise

**`GET /memory/list?session_id=default&limit=100&offset=0&filter_status=&filter_source=&search=&layer=episodic`**

Parâmetros:
- `layer`: `episodic` (padrão) | `semantic` | `all`
- `filter_status`: `verified` | `hypothesis` | `stale` | `contradicted` | `quarantined`
- `filter_source`: substring (ex: `llm`, `user`, `claude`)
- `search`: substring no texto (case-insensitive)

```json
{
  "entries": [
    {
      "id":               "uuid",
      "text":             "texto completo da memória",
      "source":           "llm:claude-haiku-4-5",
      "source_type":      "llm_response" | "user_input" | "session_summary" | "meta_conversation" | "external" | "unknown",
      "epistemic_status": "hypothesis" | "verified" | "stale" | "contradicted" | "quarantined",
      "confidence":       0.65,
      "prioridade":       "alta" | "media" | "baixa",
      "acessos":          3,
      "timestamp":        1718000000.0,
      "updated_at":       1718001000.0,
      "embedding_model":  "all-minilm",
      "layer":            "episodic" | "semantic",
      "tags":             ["tag1"],
      "human_note":       null
    }
  ],
  "count":         42,
  "total":         50,
  "layer":         "episodic",
  "filter_status": null,
  "filter_source": null,
  "search":        null
}
```

**`GET /memory/stats?session_id=default`**
```json
{
  "total":     42,
  "by_status": {
    "verified":     5,
    "hypothesis":   30,
    "stale":        4,
    "contradicted": 2,
    "quarantined":  1
  }
}
```

**`GET /memory/dominance?session_id=default&limit=20`**
```json
{
  "total_memories":   42,
  "total_retrievals": 180,
  "top_n": [
    {
      "id":               "uuid",
      "text_preview":     "primeiros 120 chars...",
      "source":           "llm:claude-haiku-4-5",
      "epistemic_status": "hypothesis",
      "acessos":          15,
      "share_pct":        8.33,
      "last_access_ts":   1718000000.0,
      "confidence":       0.65
    }
  ],
  "top_n_share": 42.5,
  "gini":        0.31,
  "warning":     null | "DOMINANCIA ALTA: ..."
}
```

**`GET /memory/topics?session_id=default`**
```json
{
  "topics": [
    {
      "tag":        "IA cognição",
      "summaries":  [ { "text": "...", "timestamp": ..., "session": "default" } ],
      "count":      3,
      "first_seen": 1718000000.0,
      "last_seen":  1718010000.0
    }
  ],
  "total": 5
}
```

**`GET /memory/{entry_id}?session_id=default`**  
Retorna o entry completo (todos os campos, incluindo embedding).

**`PATCH /memory/{entry_id}?session_id=default`**
```json
// Request
{
  "text":             "novo texto",
  "epistemic_status": "verified",
  "confidence":       0.9,
  "prioridade":       "alta",
  "source_type":      "user_input",
  "tags":             ["importante"],
  "note":             "anotação humana"
}
// Response: {"updated": true, "id": "uuid"}
```

**`DELETE /memory/{entry_id}?session_id=default`**  
Response: `{"deleted": true, "id": "uuid"}`

**`POST /memory/batch_delete?session_id=default`**
```json
// Request
{ "filter_status": "contradicted", "filter_source": null, "confirm": true }
// Response: {"deleted_count": 3}
```

**`POST /memory/add`**
```json
// Request
{ "text": "nova memória", "score": 0.5, "prioridade": "media", "session_id": "default" }
// Response: {"added": true, "id": "uuid"}
```

**`GET /memory/co_occurrence?session_id=default&id=&top_k=10`**  
Sem `id`: visão geral dos atratores. Com `id`: co-ocorrências de uma entry.

**`GET /memory/trajectory?session_id=default`**  
Grafo derivacional: raízes, folhas, abandonados, loops temáticos.

**`GET /memory/retrieve_debug?query=texto&session_id=default&top_k=10&min_score=0.05`**  
Debug do retrieval: mostra todas as memórias encontradas com scores completos.

**`POST /memory/consolidate?session_id=default&mode=promote_only&promote_threshold=3`**  
Promove memórias episódicas acessadas ≥ `threshold` vezes para camada semântica.

**`POST /memory/reclassify_all?session_id=default&dry_run=false`**  
Reclassifica `source_type` de todas as memórias existentes.

---

### 3.5 Modo Operacional

**`GET /mode/current?session_id=default`**
```json
{ "session_id": "default", "mode": "cognitive" | "sprint" }
```

**`POST /mode/{name}?session_id=default`**  
`name`: `cognitive` | `sprint`
```json
{
  "session_id": "default",
  "mode":       "sprint",
  "previous":   "cognitive",
  "message":    "Modo Sprint ativado"
}
```

---

### 3.6 Lineage (Proveniência de Respostas)

**`GET /lineage?session_id=default&last_n=30`**
```json
{
  "lineage": [
    {
      "response_id":    "uuid",
      "session_id":     "default",
      "model_used":     "claude-haiku-4-5",
      "n_sources":      3,
      "source_entries": [
        { "entry_id": "uuid", "score": 0.74, "source_type": "llm_response", "timestamp": 1718000000.0 }
      ],
      "quality_score":   0.82,
      "quality_verdict": "good",
      "timestamp":       1718001000.0
    }
  ],
  "stats": { "returned": 30, "last_n": 30 }
}
```

**`GET /lineage/{response_id}?session_id=default`**  
Aceita UUID completo ou prefixo (mínimo 8 chars). 404 se não encontrado, 409 se prefixo ambíguo.

---

### 3.7 Contradiction Flags

**`GET /flags?limit=50&offset=0&filter_feedback=`**  
`filter_feedback`: `useful` | `false_positive` | `ambiguous` | `unreviewed`
```json
{
  "flags": [
    {
      "flag_id":       "uuid",
      "id_a":          "uuid",
      "id_b":          "uuid",
      "text_a":        "texto da memória A",
      "text_b":        "texto da memória B",
      "similarity":    0.91,
      "detected_at":   1718000000.0,
      "feedback":      "unreviewed",
      "feedback_at":   null,
      "feedback_note": ""
    }
  ],
  "count": 3,
  "limit": 50
}
```

**`GET /flags/aggregates`**
```json
{
  "total":                   5,
  "by_feedback":             { "unreviewed": 3, "useful": 1, "false_positive": 1 },
  "useful_rate":             0.5,
  "review_rate":             0.4,
  "avg_similarity":          0.88,
  "useful_avg_sim":          0.91,
  "fp_avg_sim":              0.87,
  "oldest_unreviewed_age_days": 2.5
}
```

**`POST /flags/{flag_id}/feedback`**
```json
// Request
{ "feedback": "useful" | "false_positive" | "ambiguous" | "unreviewed", "note": "..." }
// Response: {"flag_id": "uuid", "feedback": "useful", "ok": true}
```

---

### 3.8 Cognitive Decisions

**`GET /cognitive_decisions?session_id=default&limit=50&offset=0&domain=`**
```json
{
  "decisions": [
    {
      "entry_id":      "uuid",
      "key_assertion": "LLMs não têm memória nativa",
      "concepts":      ["memória", "LLM", "episódico"],
      "domain":        "IA cognição",
      "extracted_at":  1718000000.0,
      "model_used":    "claude-haiku-4-5"
    }
  ],
  "stats": {
    "total":          42,
    "total_matching": 42,
    "unique_domains": 5,
    "domains":        ["IA cognição", "filosofia", ...],
    "returned":       50
  }
}
```

---

### 3.9 Métricas

**`GET /metrics`**  
Métricas de performance agregadas (lidas do log de disco).

**`GET /snapshot?session_id=default`**
```json
{
  "session_id": "default",
  "memory":     { ... },
  "metrics":    { "turns": 12, "avg_latency_ms": 1200 }
}
```

**`GET /tools`**  
Lista de tools registradas com schemas.

---

## 4. O que o Frontend Atual Faz (dashboard.js como documentação viva)

O `dashboard.js` existente (em `edp/dashboard/static/`) faz:

1. **Conexão LLM**: `POST /connect` com provider/model/url/apikey via form HTML
2. **WebSocket**: conecta a `ws://host/ws/chat/default` manualmente via form
3. **Chat**: envia `{message: texto}` via WS, recebe `start → chunk × N → done`
4. **Handlers WS implementados**: `heartbeat` (ignora), `start` (cria bolha), `chunk` (acumula texto), `pipeline_done` (atualiza stage), `llm_start` (atualiza stage), `done` (finaliza bolha)
5. **Handlers WS ignorados**: `quality`, `router_decision`, `warn`, `error`, `lineage`, `camara_iniciada`, `camara_fase_b_completa`, `camara_resultado`
6. **REST**: `GET /dashboard/state` a cada 5s para atualizar métricas do painel

---

## 5. Echo Chamber — O Que Expõe ao Frontend

O echo chamber (`edp/echo_chamber.py`) roda quando A admite limite com auto-sinal
de alta confiança. O frontend recebe:

- **3 eventos progressivos** via WS: `camara_iniciada → camara_fase_b_completa → camara_resultado`
- **O que está disponível**: texto de A (`texto_final` quando A vence), texto reformulado de B (`texto_final` quando B vence), 7 checks com PASS/FAIL e justificativa, concordância (0-100), vencedor
- **O que NÃO está disponível sem mudar o backend**: texto de B quando A vence (B reformulou mas foi rejeitado), reasoning interno de cada rodada

---

## 6. Memória / Fontes — Como Chegam ao Frontend

Duas formas:

**Via WS (tempo real, por turno):**
- Evento `pipeline_done` traz `retrieved` (3 primeiros blocos como strings)
- Evento `lineage` traz metadados das fontes (entry_id, score, source_type, timestamp) — sem o texto completo

**Via REST (qualquer momento):**
- `GET /memory/list` — lista paginada completa com todos os campos
- `GET /memory/retrieve_debug?query=...` — debug do retrieval com scores
- `GET /lineage` — histórico de proveniência por resposta

**Campos disponíveis nas fontes (retrieval):**
```
id, text, source, source_type, epistemic_status, confidence,
prioridade, acessos, timestamp, updated_at, embedding_model,
ranking_score (score calculado no retrieval), tags, human_note
```

---

## 7. Pendências de Backend (Itens que o Frontend Não Pode Obter)

Os itens abaixo são **limitações documentadas** — o frontend novo não deve
inventar dados; deve usar mocks claramente marcados ou omitir a feature.

1. **Texto de B quando A vence**: `camara_resultado` não inclui
   `texto_B_reformulacao` quando `vencedor="A"`. B reformulou mas foi
   rejeitado — esse texto existe no `CamaraRecord` em disco mas não é
   exposto no evento WS.

2. **Streaming por modelo no Compare**: A câmara de eco é síncrona do ponto
   de vista do WS. Não há eventos de chunk por modelo individual — apenas
   o resultado final.

3. **Histórico de sessões**: Não há endpoint para listar sessões passadas
   além de `GET /health.sessions` (só IDs ativos em memória). O histório
   de conversas anteriores não é recuperável via API.

4. **Busca semântica nas memórias via REST**: `GET /memory/list` filtra por
   substring, não por similaridade vetorial. Para busca por similaridade, é
   necessário `GET /memory/retrieve_debug?query=...` (endpoint de debug).
