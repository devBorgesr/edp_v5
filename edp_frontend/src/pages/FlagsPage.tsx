import { useState } from 'react'
import { useFlags, useFlagsAggregates, useFlagFeedback } from '../hooks/queries'
import PageHeader from '../components/ui/PageHeader'
import QueryState from '../components/ui/QueryState'
import Badge from '../components/ui/Badge'
import type { ContradictionFlag } from '../types/api'

const FEEDBACK_FILTERS = [
  { key: undefined,         label: 'Todos' },
  { key: 'unreviewed',      label: 'Não revisados' },
  { key: 'useful',          label: 'Úteis' },
  { key: 'false_positive',  label: 'Falsos positivos' },
  { key: 'ambiguous',       label: 'Ambíguos' },
] as const

const FEEDBACK_COLOR: Record<string, string> = {
  unreviewed: 'var(--color-muted)',
  useful: 'var(--color-ok)',
  false_positive: 'var(--color-err)',
  ambiguous: 'var(--color-warn)',
}

function FlagCard({ flag }: { flag: ContradictionFlag }) {
  const fb = useFlagFeedback()
  const buttons: { value: string; label: string; color: string }[] = [
    { value: 'useful', label: 'Útil', color: 'var(--color-ok)' },
    { value: 'false_positive', label: 'Falso +', color: 'var(--color-err)' },
    { value: 'ambiguous', label: 'Ambíguo', color: 'var(--color-warn)' },
  ]

  return (
    <div
      className="rounded-lg border p-3"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <div className="flex items-center justify-between mb-2">
        <Badge
          label={flag.feedback}
          color={FEEDBACK_COLOR[flag.feedback]}
          bg="var(--color-panel)"
        />
        <span className="text-[10px]" style={{ color: 'var(--color-muted)' }}>
          similaridade {(flag.similarity * 100).toFixed(0)}%
        </span>
      </div>

      <div className="space-y-1.5">
        <div className="flex gap-2">
          <span className="text-[10px] mt-0.5 flex-shrink-0" style={{ color: 'var(--color-accent2)' }}>A</span>
          <p className="text-xs" style={{ color: 'var(--color-text)' }}>{flag.text_a}</p>
        </div>
        <div className="flex gap-2">
          <span className="text-[10px] mt-0.5 flex-shrink-0" style={{ color: 'var(--color-accent)' }}>B</span>
          <p className="text-xs" style={{ color: 'var(--color-text)' }}>{flag.text_b}</p>
        </div>
      </div>

      <div className="flex gap-1.5 mt-3">
        {buttons.map(b => (
          <button
            key={b.value}
            onClick={() => fb.mutate({ id: flag.flag_id, feedback: b.value })}
            disabled={fb.isPending}
            className="text-[11px] px-2 py-0.5 rounded border transition-opacity hover:opacity-80 disabled:opacity-40"
            style={{
              borderColor: 'var(--color-border)',
              color: flag.feedback === b.value ? '#fff' : b.color,
              background: flag.feedback === b.value ? b.color : 'transparent',
            }}
          >
            {b.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function FlagsPage() {
  const [filter, setFilter] = useState<string | undefined>(undefined)
  const list = useFlags(filter)
  const agg = useFlagsAggregates()
  const flags = list.data?.flags ?? []

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Flags / Contradições"
        subtitle={agg.data ? `${agg.data.total} no total · ${(agg.data.useful_rate * 100).toFixed(0)}% úteis` : 'pares contraditórios detectados'}
        right={
          agg.data && (
            <div className="flex gap-1.5">
              <Badge label={`sim. média ${(agg.data.avg_similarity * 100).toFixed(0)}%`} />
              <Badge label={`revisados ${(agg.data.review_rate * 100).toFixed(0)}%`} />
            </div>
          )
        }
      />

      <div
        className="flex items-center gap-1 px-5 py-2.5 border-b flex-shrink-0"
        style={{ borderColor: 'var(--color-border)' }}
      >
        {FEEDBACK_FILTERS.map(f => (
          <button
            key={f.label}
            onClick={() => setFilter(f.key)}
            className="px-2.5 py-1 rounded-md text-xs transition-colors"
            style={{
              background: filter === f.key ? 'rgba(124,58,237,0.2)' : 'transparent',
              color: filter === f.key ? '#fff' : 'var(--color-muted)',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-2">
          <QueryState
            isLoading={list.isLoading}
            error={list.error}
            isEmpty={flags.length === 0}
            emptyLabel="Nenhuma contradição registrada."
          >
            {flags.map(f => <FlagCard key={f.flag_id} flag={f} />)}
          </QueryState>
        </div>
      </div>
    </div>
  )
}
