import { useState } from 'react'
import type { WsLineage } from '../../types/api'

export default function LineagePanel({ lineage }: { lineage: WsLineage }) {
  const [open, setOpen] = useState(false)
  const uiSources = lineage.sources_ui ?? []
  const hasUi = uiSources.length > 0

  return (
    <div
      className="mt-2 rounded-lg border text-xs animate-fadein"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-panel)' }}
    >
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2">
          <span style={{ color: 'var(--color-accent2)' }}>⊡ Memória usada</span>
          <span style={{ color: 'var(--color-muted)' }}>
            {lineage.n_sources} fonte{lineage.n_sources === 1 ? '' : 's'}
          </span>
          {!!lineage.conflict_count && lineage.conflict_count > 0 && (
            <span style={{ color: 'var(--color-warn)' }}>
              ⚠ {lineage.conflict_count} conflito{lineage.conflict_count === 1 ? '' : 's'}
            </span>
          )}
        </span>
        <span style={{ color: 'var(--color-muted)' }}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2 border-t" style={{ borderColor: 'var(--color-border)' }}>
          {hasUi
            ? uiSources.map((s, i) => (
                <div key={i} className="flex items-start gap-2">
                  <span
                    className="mt-0.5 flex-shrink-0 px-1 rounded text-[10px]"
                    style={{ background: 'var(--color-surface)', color: 'var(--color-muted)' }}
                  >
                    {(s.score * 100).toFixed(0)}%
                  </span>
                  <div className="min-w-0">
                    <p style={{ color: 'var(--color-text)' }} className="truncate">{s.text}</p>
                    <p style={{ color: 'var(--color-muted)' }} className="text-[10px]">
                      {s.source_type} · {s.epistemic} · {s.rel_time}
                    </p>
                  </div>
                </div>
              ))
            : lineage.sources.map((s, i) => (
                <div key={i} className="flex items-center gap-2" style={{ color: 'var(--color-muted)' }}>
                  <span
                    className="px-1 rounded text-[10px]"
                    style={{ background: 'var(--color-surface)' }}
                  >
                    {(s.score * 100).toFixed(0)}%
                  </span>
                  <span className="truncate font-mono text-[10px]">{s.entry_id}</span>
                  <span>· {s.source_type}</span>
                </div>
              ))}
        </div>
      )}
    </div>
  )
}
