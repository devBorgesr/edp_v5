// Cliente REST tipado para o EDP v5.
// Todas as chamadas passam pelo proxy Vite → backend :8000.
// Nenhuma URL hardcoded com host — usa caminhos relativos.

import type {
  ConnectRequest, ConnectResponse, DashboardState, DominanceResponse,
  FlagsAggregates, FlagsResponse, HealthResponse, LineageListResponse,
  LineageRecord, MemoryEntry, MemoryListResponse, MemoryStats,
  ModeResponse, ProvidersResponse, TopicsResponse,
} from '../types/api'

const SESSION = 'default'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`POST ${path} → ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PATCH ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: 'DELETE' })
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

// ── Health ────────────────────────────────────────────────────────────────────
export const health = {
  get: () => get<HealthResponse>('/health'),
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export const dashboard = {
  state: (sid = SESSION) => get<DashboardState>(`/dashboard/state?session_id=${sid}`),
}

// ── LLM / Providers ──────────────────────────────────────────────────────────
export const providers = {
  list: () => get<ProvidersResponse>('/providers'),
}

export const llm = {
  connect: (req: ConnectRequest) => post<ConnectResponse>('/connect', req),
}

// ── Mode ─────────────────────────────────────────────────────────────────────
export const mode = {
  current: (sid = SESSION) => get<ModeResponse>(`/mode/current?session_id=${sid}`),
  set: (name: 'cognitive' | 'sprint', sid = SESSION) =>
    post<ModeResponse>(`/mode/${name}?session_id=${sid}`),
}

// ── Memory ───────────────────────────────────────────────────────────────────
export const memory = {
  list: (params?: {
    limit?: number
    offset?: number
    layer?: 'episodic' | 'semantic' | 'all'
    filter_status?: string
    filter_source?: string
    search?: string
    sid?: string
  }) => {
    const p = params ?? {}
    const sid = p.sid ?? SESSION
    const qs = new URLSearchParams({ session_id: sid })
    if (p.limit)         qs.set('limit', String(p.limit))
    if (p.offset)        qs.set('offset', String(p.offset))
    if (p.layer)         qs.set('layer', p.layer)
    if (p.filter_status) qs.set('filter_status', p.filter_status)
    if (p.filter_source) qs.set('filter_source', p.filter_source)
    if (p.search)        qs.set('search', p.search)
    return get<MemoryListResponse>(`/memory/list?${qs}`)
  },

  stats: (sid = SESSION) => get<MemoryStats>(`/memory/stats?session_id=${sid}`),

  dominance: (limit = 20, sid = SESSION) =>
    get<DominanceResponse>(`/memory/dominance?session_id=${sid}&limit=${limit}`),

  topics: (sid = SESSION) => get<TopicsResponse>(`/memory/topics?session_id=${sid}`),

  get: (entryId: string, sid = SESSION) =>
    get<MemoryEntry>(`/memory/${entryId}?session_id=${sid}`),

  update: (entryId: string, body: Partial<MemoryEntry>, sid = SESSION) =>
    patch<{ updated: boolean; id: string }>(`/memory/${entryId}?session_id=${sid}`, body),

  delete: (entryId: string, sid = SESSION) =>
    del<{ deleted: boolean; id: string }>(`/memory/${entryId}?session_id=${sid}`),

  batchDelete: (filter: { filter_status?: string; filter_source?: string }, sid = SESSION) =>
    post<{ deleted_count: number }>(`/memory/batch_delete?session_id=${sid}`, {
      ...filter,
      confirm: true,
    }),

  consolidate: (sid = SESSION) =>
    post<Record<string, unknown>>(`/memory/consolidate?session_id=${sid}&mode=promote_only`),
}

// ── Lineage ──────────────────────────────────────────────────────────────────
export const lineage = {
  list: (lastN = 30, sid = SESSION) =>
    get<LineageListResponse>(`/lineage?session_id=${sid}&last_n=${lastN}`),
  detail: (responseId: string, sid = SESSION) =>
    get<LineageRecord>(`/lineage/${responseId}?session_id=${sid}`),
}

// ── Flags ────────────────────────────────────────────────────────────────────
export const flags = {
  list: (params?: { limit?: number; filter_feedback?: string }) => {
    const qs = new URLSearchParams()
    if (params?.limit)           qs.set('limit', String(params.limit))
    if (params?.filter_feedback) qs.set('filter_feedback', params.filter_feedback)
    return get<FlagsResponse>(`/flags?${qs}`)
  },
  aggregates: () => get<FlagsAggregates>('/flags/aggregates'),
  feedback: (flagId: string, feedback: string, note = '') =>
    post<{ ok: boolean }>(`/flags/${flagId}/feedback`, { feedback, note }),
}
