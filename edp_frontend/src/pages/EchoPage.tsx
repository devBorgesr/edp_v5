import { useLineage } from '../hooks/queries'
import PageHeader from '../components/ui/PageHeader'
import QueryState from '../components/ui/QueryState'
import Badge from '../components/ui/Badge'
import type { LineageRecord } from '../types/api'

function relTime(ts?: number): string {
  if (!ts) return ''
  const secs = Math.floor(Date.now() / 1000 - ts)
  if (secs < 60) return `${secs}s atrás`
  if (secs < 3600) return `${Math.floor(secs / 60)}min atrás`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h atrás`
  return `${Math.floor(secs / 86400)}d atrás`
}

const VERDICT_COLOR: Record<string, string> = {
  excellent: 'var(--color-ok)',
  good: 'var(--color-ok)',
  ok: 'var(--color-accent2)',
  poor: 'var(--color-warn)',
  degraded: 'var(--color-err)',
}

function LineageCard({ rec }: { rec: LineageRecord }) {
  return (
    <div
      className="rounded-lg border p-3"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Badge label={rec.model_used} color="var(--color-accent)" bg="rgba(124,58,237,0.12)" />
          <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
            {rec.n_sources} fonte{rec.n_sources === 1 ? '' : 's'}
          </span>
          {rec.quality_verdict && (
            <Badge
              label={rec.quality_verdict}
              color={VERDICT_COLOR[rec.quality_verdict] ?? 'var(--color-muted)'}
              bg="var(--color-panel)"
            />
          )}
          {rec.quality_score != null && (
            <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
              score {rec.quality_score.toFixed(2)}
            </span>
          )}
        </div>
        <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--color-muted)' }}>
          {relTime(rec.timestamp)}
        </span>
      </div>

      {rec.source_entries.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {rec.source_entries.map((s, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded font-mono"
              style={{ background: 'var(--color-panel)', color: 'var(--color-muted)' }}
              title={`${s.source_type} · ${(s.score * 100).toFixed(0)}%`}
            >
              {s.entry_id.slice(0, 8)} · {(s.score * 100).toFixed(0)}%
            </span>
          ))}
        </div>
      )}

      <p className="text-[10px] mt-2 font-mono truncate" style={{ color: 'var(--color-muted)' }}>
        {rec.response_id}
      </p>
    </div>
  )
}

export default function EchoPage() {
  const { data, isLoading, error } = useLineage(50)
  const records = data?.lineage ?? []

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Câmara / Lineage"
        subtitle="histórico de proveniência das respostas — qual memória alimentou cada resposta"
        right={data && <Badge label={`${data.stats.returned} registros`} />}
      />
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-2">
          <QueryState
            isLoading={isLoading}
            error={error}
            isEmpty={records.length === 0}
            emptyLabel="Nenhum registro de lineage ainda. Converse no chat para gerar."
          >
            {records.map(r => <LineageCard key={r.response_id} rec={r} />)}
          </QueryState>
        </div>
      </div>
    </div>
  )
}
