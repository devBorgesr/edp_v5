// Tipos derivados de CONTRATO_API_EDP.md
// Qualquer mudança no backend deve refletir aqui.

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket — Mensagens Servidor → Cliente
// ─────────────────────────────────────────────────────────────────────────────

export type WsMessageType =
  | 'start'
  | 'quality'
  | 'pipeline_done'
  | 'router_decision'
  | 'llm_start'
  | 'warn'
  | 'chunk'
  | 'camara_iniciada'
  | 'camara_fase_b_completa'
  | 'camara_resultado'
  | 'lineage'
  | 'done'
  | 'error'
  | 'heartbeat'

export interface WsStart {
  type: 'start'
  session_id: string
  stage: 'pipeline' | 'command'
}

export interface WsQuality {
  type: 'quality'
  score: number
  verdict: 'excellent' | 'good' | 'ok' | 'poor' | 'degraded'
  components: Record<string, number>
  source: string
}

export interface WsPipelineDone {
  type: 'pipeline_done'
  compression_pct: number
  memory_hits: number
  retrieved: string[]
  pipeline_ok: boolean
}

export interface WsRouterDecision {
  type: 'router_decision'
  model: string
  tier: 'haiku' | 'sonnet' | 'opus'
  reason: string
  cost: number
  badge: string
}

export interface WsLlmStart {
  type: 'llm_start'
  model: string
}

export interface WsWarn {
  type: 'warn'
  error: string
}

export interface WsChunk {
  type: 'chunk'
  text: string
}

export type CheckVerdict = 'PASS' | 'FAIL'
export interface CheckInfo {
  verdict: CheckVerdict
  justificativa: string
}
export type CamaraChecks = Record<string, CheckInfo>

export interface WsCamaraIniciada {
  type: 'camara_iniciada'
  camara_id: string
  modelo_A: string
  modelo_B: string
  trecho_A: string
}

export interface WsCamaraFaseBCompleta {
  type: 'camara_fase_b_completa'
  camara_id: string
  modelo_B: string
  checks: CamaraChecks
  latencia_ms_b: number
  tem_reformulacao: boolean
}

export interface WsCamaraResultado {
  type: 'camara_resultado'
  texto_final: string
  texto_A?: string
  modelo_A: string
  modelo_B: string
  vencedor: 'A' | 'B' | 'ambos_similar'
  concordancia: number
  score_A?: { score: number; max_score: number; pass_count: number; fail_count: number }
  camara_id: string
}

export interface LineageSource {
  entry_id: string
  score: number
  source_type: string
  timestamp: number
}

export interface WsLineage {
  type: 'lineage'
  response_id: string
  n_sources: number
  sources: LineageSource[]
  sources_ui?: SourceUiEntry[]
  conflict_count?: number
  model_used: string
}

export interface SourceUiEntry {
  text: string
  rel_time: string
  source_type: string
  epistemic: string
  score: number
}

export interface WsDone {
  type: 'done'
  text: string
  llm_used: boolean
  memory_hits: number
  compression_pct: number
  metrics: Record<string, unknown>
}

export interface WsError {
  type: 'error'
  error: string
}

export interface WsHeartbeat {
  type: 'heartbeat'
}

export type WsMessage =
  | WsStart
  | WsQuality
  | WsPipelineDone
  | WsRouterDecision
  | WsLlmStart
  | WsWarn
  | WsChunk
  | WsCamaraIniciada
  | WsCamaraFaseBCompleta
  | WsCamaraResultado
  | WsLineage
  | WsDone
  | WsError
  | WsHeartbeat

// ─────────────────────────────────────────────────────────────────────────────
// REST — Shapes de resposta
// ─────────────────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  version: string
  timestamp: number
  sessions: string[]
  metrics: Record<string, unknown>
}

export interface DashboardState {
  timestamp: number
  session_id: string
  runtime_state: {
    state: 'READY' | 'WARMING' | 'DEGRADED' | 'COLD' | 'SHUTDOWN' | 'unknown'
    components?: Record<string, boolean>
    error?: string | null
  }
  pressure: {
    level: 'ok' | 'elevated' | 'critical' | 'unknown'
    available_gb: number
    used_pct: number
    error?: string
  }
  queue: {
    active?: number
    waiting?: number
    completed?: number
    error?: string
  }
  retrieval_quality?: {
    avg_score?: number
    hit_rate?: number
    error?: string
  }
  contradictions?: {
    stats: { total: number; unreviewed: number }
    recent: unknown[]
    error?: string
  }
  health: {
    status: string
    version: string
    sessions: string[]
    error?: string
  }
  memory: {
    episodic?: number
    semantic?: number
    total?: number
    error?: string
  }
  llm_metrics: Record<string, unknown>
}

export interface MemoryEntry {
  id: string
  text: string
  source?: string
  source_type?: string
  epistemic_status?: 'verified' | 'hypothesis' | 'stale' | 'contradicted' | 'quarantined'
  confidence?: number
  prioridade?: 'alta' | 'media' | 'baixa'
  acessos?: number
  timestamp?: number
  updated_at?: number
  embedding_model?: string
  layer?: 'episodic' | 'semantic'
  tags?: string[]
  human_note?: string | null
}

export interface MemoryListResponse {
  entries: MemoryEntry[]
  count: number
  total: number
  layer: string
  filter_status?: string | null
  filter_source?: string | null
  search?: string | null
}

export interface MemoryStats {
  total: number
  by_status: Record<string, number>
}

export interface DominanceEntry {
  id: string
  text_preview: string
  source: string
  epistemic_status: string
  acessos: number
  share_pct: number
  last_access_ts: number
  confidence: number
}

export interface DominanceResponse {
  total_memories: number
  total_retrievals: number
  top_n: DominanceEntry[]
  top_n_share: number
  gini: number
  warning: string | null
}

export interface TopicSummary {
  text: string
  timestamp: number
  session: string
}

export interface Topic {
  tag: string
  summaries: TopicSummary[]
  count: number
  first_seen: number
  last_seen: number
}

export interface TopicsResponse {
  topics: Topic[]
  total: number
}

export interface ConnectRequest {
  provider: 'anthropic' | 'ollama' | 'openai' | 'lm_studio'
  model: string
  base_url?: string
  api_key?: string
  session_id?: string
}

export interface ConnectResponse {
  connected: boolean
  provider: string
  model: string
  models: string[]
}

export interface ProvidersResponse {
  available: string[]
  anthropic_models: string[]
  has_anthropic_key_in_env: boolean
}

export interface ModeResponse {
  session_id: string
  mode: 'cognitive' | 'sprint'
  previous?: string
  message?: string
}

export interface LineageRecord {
  response_id: string
  session_id: string
  model_used: string
  n_sources: number
  source_entries: LineageSource[]
  quality_score?: number
  quality_verdict?: string
  timestamp: number
}

export interface LineageListResponse {
  lineage: LineageRecord[]
  stats: { returned: number; last_n: number }
}

export interface ContradictionFlag {
  flag_id: string
  id_a: string
  id_b: string
  text_a: string
  text_b: string
  similarity: number
  detected_at: number
  feedback: 'unreviewed' | 'useful' | 'false_positive' | 'ambiguous'
  feedback_at?: number | null
  feedback_note?: string
}

export interface FlagsResponse {
  flags: ContradictionFlag[]
  count: number
  limit: number
  offset: number
}

export interface FlagsAggregates {
  total: number
  by_feedback: Record<string, number>
  useful_rate: number
  review_rate: number
  avg_similarity: number
  useful_avg_sim?: number
  fp_avg_sim?: number
  oldest_unreviewed_age_days?: number
}
