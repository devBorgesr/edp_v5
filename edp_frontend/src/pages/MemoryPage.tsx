import { useState } from 'react'
import { useMemoryList, useMemoryStats, useDeleteMemory, type MemoryFilters } from '../hooks/queries'
import PageHeader from '../components/ui/PageHeader'
import QueryState from '../components/ui/QueryState'
import Badge from '../components/ui/Badge'
import { epistemicColor } from '../components/ui/epistemic'
import type { MemoryEntry } from '../types/api'

const LAYERS: MemoryFilters['layer'][] = ['all', 'episodic', 'semantic']

function relTime(ts?: number): string {
  if (!ts) return ''
  const secs = Math.floor(Date.now() / 1000 - ts)
  if (secs < 60) return `${secs}s atrás`
  if (secs < 3600) return `${Math.floor(secs / 60)}min atrás`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h atrás`
  return `${Math.floor(secs / 86400)}d atrás`
}

function MemoryRow({ entry }: { entry: MemoryEntry }) {
  const del = useDeleteMemory()
  const ep = epistemicColor(entry.epistemic_status)
  return (
    <div
      className="rounded-lg border p-3 group"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm flex-1 min-w-0" style={{ color: 'var(--color-text)' }}>
          {entry.text}
        </p>
        <button
          onClick={() => {
            if (confirm('Apagar esta entrada de memória?')) del.mutate(entry.id)
          }}
          disabled={del.isPending}
          className="opacity-0 group-hover:opacity-100 transition-opacity text-xs flex-shrink-0 px-2 py-0.5 rounded"
          style={{ color: 'var(--color-err)', background: 'rgba(239,68,68,0.1)' }}
        >
          apagar
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 mt-2">
        {entry.epistemic_status && (
          <Badge label={entry.epistemic_status} color={ep.color} bg={ep.bg} />
        )}
        {entry.layer && <Badge label={entry.layer} />}
        {entry.source && <Badge label={entry.source} />}
        {entry.prioridade && <Badge label={`prio: ${entry.prioridade}`} />}
        {entry.confidence != null && <Badge label={`conf ${(entry.confidence * 100).toFixed(0)}%`} />}
        {entry.acessos != null && <Badge label={`${entry.acessos} acessos`} />}
        <span className="text-[10px] ml-auto" style={{ color: 'var(--color-muted)' }}>
          {relTime(entry.timestamp)}
        </span>
      </div>
    </div>
  )
}

export default function MemoryPage() {
  const [layer, setLayer] = useState<MemoryFilters['layer']>('all')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')

  const filters: MemoryFilters = { layer, limit: 100, search: query || undefined }
  const list = useMemoryList(filters)
  const stats = useMemoryStats()

  const entries = list.data?.entries ?? []

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Memória"
        subtitle={
          list.data
            ? `${list.data.count} de ${list.data.total} entradas`
            : 'episódica + semântica'
        }
        right={
          stats.data && (
            <div className="flex gap-1.5">
              {Object.entries(stats.data.by_status).map(([k, v]) => {
                const ep = epistemicColor(k)
                return <Badge key={k} label={`${k}: ${v}`} color={ep.color} bg={ep.bg} />
              })}
            </div>
          )
        }
      />

      {/* Filtros */}
      <div
        className="flex items-center gap-2 px-5 py-2.5 border-b flex-shrink-0"
        style={{ borderColor: 'var(--color-border)' }}
      >
        <div className="flex gap-1">
          {LAYERS.map(l => (
            <button
              key={l}
              onClick={() => setLayer(l)}
              className="px-2.5 py-1 rounded-md text-xs transition-colors"
              style={{
                background: layer === l ? 'rgba(124,58,237,0.2)' : 'transparent',
                color: layer === l ? '#fff' : 'var(--color-muted)',
              }}
            >
              {l}
            </button>
          ))}
        </div>
        <form
          className="flex-1 flex gap-2"
          onSubmit={e => {
            e.preventDefault()
            setQuery(search.trim())
          }}
        >
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Buscar no texto…"
            className="flex-1 rounded-md border px-3 py-1 text-xs outline-none"
            style={{
              background: 'var(--color-surface)',
              borderColor: 'var(--color-border)',
              color: 'var(--color-text)',
            }}
          />
          {query && (
            <button
              type="button"
              onClick={() => { setSearch(''); setQuery('') }}
              className="text-xs px-2" style={{ color: 'var(--color-muted)' }}
            >
              limpar
            </button>
          )}
        </form>
      </div>

      {/* Lista */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-2">
          <QueryState
            isLoading={list.isLoading}
            error={list.error}
            isEmpty={entries.length === 0}
            emptyLabel="Nenhuma entrada de memória encontrada."
          >
            {entries.map(e => <MemoryRow key={e.id} entry={e} />)}
          </QueryState>
        </div>
      </div>
    </div>
  )
}
