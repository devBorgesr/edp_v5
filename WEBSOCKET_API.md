# WebSocket API — Live Feed (receptor de eventos do sensor)

Porta de entrada em tempo real para eventos gerados por um sensor externo
(extensão de navegador/IDE): thinking, respostas, tool calls. O EDP
armazena cada evento na memória episódica, expõe uma API HTTP para
consultá-los, e gera automaticamente um resumo (armazenado na memória
semântica) quando uma conversa termina.

A comunicação é **estritamente unidirecional** (sensor → EDP). O EDP nunca
envia comandos de volta ao sensor — só `pong` (heartbeat) e `error`
(evento inválido/falha de processamento). Isso é deliberado: evita que o
EDP automatize ações na ferramenta que está sendo observada.

## Endpoint WebSocket: `/stream`

```
ws://<host>:<port>/stream
```

### Autenticação

Controlada por `EDP_LIVE_FEED_TOKEN` (env var). **Se não configurada, o
endpoint aceita conexões sem autenticação** — mesma postura dev-friendly
do resto da API do EDP (`CORS allow_origins=["*"]`). Configure o token
antes de expor o EDP fora de localhost.

Duas formas de enviar o token, escolha uma:

1. **Query string** (mais simples, mas o token fica em logs de acesso do
   servidor/proxy):
   ```
   ws://127.0.0.1:8000/stream?token=<EDP_LIVE_FEED_TOKEN>
   ```

2. **Sec-WebSocket-Protocol** (preferível — não aparece em logs de acesso
   HTTP comuns). Passe o token como o subprotocolo oferecido pelo cliente:
   ```js
   new WebSocket("ws://127.0.0.1:8000/stream", ["<EDP_LIVE_FEED_TOKEN>"]);
   ```
   O servidor ecoa o subprotocolo de volta no handshake (exigido pelos
   clientes `WebSocket` nativos de navegador, RFC 6455 §4.2.2).

Token ausente/incorreto quando `EDP_LIVE_FEED_TOKEN` está configurada →
conexão fechada com código `4401`.

### Formato do evento

```jsonc
{
  "type": "thinking" | "response" | "tool_call" | "tool_result" | "response_end" | "ping" | ...,
  "timestamp": 1735689600.123,       // epoch seconds, relógio do sensor
  "conversation_id": "conv_abc123",  // obrigatório — identifica a conversa
  "session_id": "sess_xyz",          // opcional — metadado, não usado p/ roteamento
  "profile_id": "user_42",           // opcional — usado em GET /memory/sessions
  "data": { "text": "..." }          // conteúdo do evento (objeto)
}
```

Campos obrigatórios: `type`, `timestamp`, `conversation_id`, `data`.

#### Escala do `timestamp` (contrato + tolerância)

**A convenção é epoch SECONDS** — a mesma do `timestamp` interno do EDP,
do dashboard e dos testes. Um `timestamp` numérico acima de `1e12` é
interpretado como epoch **milissegundos** e **convertido para segundos**
na recepção (`normalize_timestamp`, `edp/ingest/events.py`), com um
`logger.warning` em `live_feed.log` identificando a conversa e o tipo de
evento.

O EDP **não rejeita** milissegundos, de propósito: o `/stream` é
unidirecional e o sensor descarta toda resposta do servidor que não seja
`pong`, então uma rejeição seria — do lado do emissor — indistinguível de
sucesso. É exatamente o modo de falha do incidente de 2026-08-03
(`toISOString()` → rejeição → perda de 100% dos eventos sem ninguém
perceber). Coagir preserva o dado; o warning torna o desalinhamento
mensurável em vez de invisível.

O limiar `1e12` separa as duas escalas com folga: hoje, segundos ≈ 1,8e9
e milissegundos ≈ 1,8e12 — a ambiguidade só reapareceria no ano ~33658.

Rejeitados (com `{"type":"error"}`, conexão mantida): `timestamp`
não-numérico, booleano, `NaN`/infinito, ou ≤ 0.

Histórico: o sensor emitiu milissegundos (`Date.now()`) até 06/08/2026;
a partir dessa data emite segundos (`Date.now() / 1000`). A tolerância
acima cobre versões antigas da extensão e eventos que ficaram no buffer
local dela antes da atualização.
`session_id`/`profile_id`, se presentes, podem estar no nível superior do
evento OU dentro de `data` — o EDP procura nos dois lugares.

`conversation_id`, `session_id` e `profile_id` (quando presentes) devem
bater com `^[A-Za-z0-9_\-]{1,128}$`. Eles viram nomes de diretório na
persistência da memória — qualquer coisa fora desse formato é rejeitada
(evento inválido, conexão permanece aberta).

**`conversation_id` é a chave de agrupamento.** É ele — não `session_id` —
que determina em qual "sessão" de memória o evento é armazenado, porque é
o único campo obrigatório. `session_id`, quando enviado, é guardado como
metadado adicional no evento, nunca usado para roteamento.

### Heartbeat

```jsonc
// cliente envia:
{"type": "ping"}
// servidor responde:
{"type": "pong"}
```

Pings não são validados como evento nem gravados no log de auditoria.

### Respostas de erro

Evento malformado ou falha ao processar → o servidor responde e **mantém a
conexão aberta**:

```jsonc
{"type": "error", "error": "campo obrigatório ausente: conversation_id"}
```

### Como os eventos são armazenados

Cada evento válido vira uma entrada na memória episódica da conversa
(`source="live_feed"`), com metadados: `event_type` (o `type` do evento),
`event_timestamp` (o timestamp do sensor — o `timestamp` interno do EDP,
usado para decaimento/detecção de fronteira de sessão, nunca é
sobrescrito), `conversation_id`, `session_id`, `profile_id`.

Eventos `response_end`, `tool_call` e `tool_result` recebem um score
levemente maior que eventos finos (`thinking`) — dá ao mecanismo de poda
da memória episódica (`_prune()`) um critério melhor que "o mais antigo
sobrevive" quando a conversa excede o limite de entradas (ver
"Limitações conhecidas" abaixo).

### Consolidação automática (`response_end`)

Ao receber um evento `type == "response_end"`, o EDP dispara — em thread
separada, sem bloquear novos eventos — uma consolidação da conversa:

1. Se houver um LLM conectado na sessão correspondente, gera um resumo via
   `edp.session_summary.generate_session_summary()` (o mesmo mecanismo já
   usado ao encerrar uma conversa de chat).
2. **Se nenhum LLM estiver conectado** (o caso comum — nada chama
   `/connect` para uma sessão do live feed), cai para um resumo
   extrativo/heurístico sem LLM (concatena o texto recente dos eventos,
   sem chamada externa). Garante que `response_end` sempre produza um
   resumo armazenado, mesmo sem LLM configurado.
3. O resumo resultante é promovido para a **memória semântica**
   (`SemanticMemory.promote()`).

## Endpoints HTTP

### `GET /memory/sessions?profile_id=<id>`

Lista as conversas (`conversation_id`) já vistas para um `profile_id`.

```json
{
  "profile_id": "user_42",
  "sessions": [
    {"session_id": "conv_abc123", "last_seen": 1735689600.1}
  ],
  "count": 1
}
```

### `GET /memory/session/{session_id}`

Todos os eventos de uma conversa, ordenados por `event_timestamp`.
`session_id` aqui é o `conversation_id` do evento original. Suporta
`limit` (default 200, máx 2000) e `offset`.

```json
{
  "session_id": "conv_abc123",
  "count": 2,
  "total": 2,
  "events": [
    {"id": "...", "text": "...", "event_type": "thinking", "event_timestamp": 1735689600.1, ...},
    {"id": "...", "text": "...", "event_type": "response_end", "event_timestamp": 1735689605.3, ...}
  ]
}
```

### `GET /memory/session/{session_id}/summary`

O resumo mais recente gerado pela consolidação (`response_end`) daquela
conversa. `404` se ainda não houver resumo.

```json
{
  "session_id": "conv_abc123",
  "summary": "...",
  "topic_tag": "bitcoin_mining",
  "source": "llm:claude-haiku-4-5",
  "layer": "semantic",
  "timestamp": 1735689610.0
}
```

## Configuração (env vars)

| Variável | Default | Descrição |
|---|---|---|
| `EDP_LIVE_FEED_TOKEN` | (vazio, aberto) | Token exigido no WS `/stream`. |
| `EDP_EPISODIC_SIZE` | `200` | Limite de entradas episódicas **por conversa**. Conversas longas/faladeiras (muitos eventos `thinking`) devem subir esse valor — ver limitação abaixo. |
| `EDP_BASE_DIR` | `/content/edp_v3_memory` | Raiz de persistência; `live_feed.log` e o índice reverso ficam aqui. |

CORS já é aberto (`allow_origins=["*"]`) na configuração padrão do EDP —
nenhuma mudança extra é necessária para a extensão se conectar a partir de
um contexto de navegador, além de possivelmente configurar o token acima.

## Limitações conhecidas

- **Pruning por conversa longa**: `EpisodicMemory._prune()` usa um sort
  estável; eventos com score/prioridade empatados mantêm os **mais
  antigos** quando o limite (`EDP_EPISODIC_SIZE`, default 200) é excedido.
  Para uma conversa muito longa isso pode descartar atividade recente
  antes da antiga. Suba `EDP_EPISODIC_SIZE` para sessões de live feed
  esperadas como longas.
- **Sem LLM conectado**: o resumo de `response_end` usa o fallback
  heurístico (sem LLM) por padrão, já que nada conecta um provider numa
  sessão de live feed automaticamente. Qualidade é propositalmente básica
  (extrativa) — conecte um LLM na sessão (`registry_key` =
  `livefeed_<conversation_id>`) via `/connect` se quiser resumos via LLM.
- **Sem despejo de sessões**: cada `conversation_id` cria um `MemoryStore`
  (e, se um resumo via LLM rodar, um `EDPRuntime`) que vive pelo tempo de
  vida do processo — mesmo comportamento de qualquer outro `session_id` no
  EDP hoje, mas mais exposto aqui por causa do volume potencial de
  conversas. O índice reverso (`live_feed_index.json`) já guarda
  `last_seen` por conversa; uma futura rotina de limpeza (job periódico,
  como `auto_consolidation` em `edp/runtime/background_loop.py`) pode
  usá-lo para despejar sessões inativas — não implementado ainda.
