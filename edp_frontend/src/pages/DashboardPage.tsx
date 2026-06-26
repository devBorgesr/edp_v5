import { useDashboard } from '../hooks/queries'
import PageHeader from '../components/ui/PageHeader'
import QueryState from '../components/ui/QueryState'
import type { ReactNode } from 'react'

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <p className="text-[10px] uppercase tracking-wider mb-2" style={{ color: 'var(--color-muted)' }}>
        {title}
      </p>
      {children}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div className="flex items-baseline justify-between py-0.5">
      <span className="text-xs" style={{ color: 'var(--color-muted)' }}>{label}</span>
      <span className="text-sm font-medium" style={{ color: color ?? 'var(--color-text)' }}>{value}</span>
    </div>
  )
}

const STATE_COLOR: Record<string, string> = {
  READY: 'var(--color-ok)',
  WARMING: 'var(--color-warn)',
  DEGRADED: 'var(--color-warn)',
  COLD: 'var(--color-muted)',
  SHUTDOWN: 'var(--color-err)',
  unknown: 'var(--color-muted)',
}

const PRESSURE_COLOR: Record<string, string> = {
  ok: 'var(--color-ok)',
  elevated: 'var(--color-warn)',
  critical: 'var(--color-err)',
  unknown: 'var(--color-muted)',
}

export default function DashboardPage() {
  const { data, isLoading, error } = useDashboard()

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Dashboard" subtitle="estado do sistema · atualiza a cada 5s" />
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <QueryState isLoading={isLoading} error={error}>
            {data && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                <Card title="Runtime">
                  <Stat
                    label="Estado"
                    value={data.runtime_state.state}
                    color={STATE_COLOR[data.runtime_state.state] ?? 'var(--color-text)'}
                  />
                  {data.runtime_state.components &&
                    Object.entries(data.runtime_state.components).map(([k, v]) => (
                      <Stat key={k} label={k} value={v ? '✓' : '✗'}
                        color={v ? 'var(--color-ok)' : 'var(--color-err)'} />
                    ))}
                </Card>

                <Card title="Pressão de memória">
                  <Stat label="Nível" value={data.pressure.level}
                    color={PRESSURE_COLOR[data.pressure.level] ?? 'var(--color-text)'} />
                  <Stat label="Disponível" value={`${data.pressure.available_gb?.toFixed(1)} GB`} />
                  <Stat label="Uso" value={`${data.pressure.used_pct?.toFixed(0)}%`} />
                </Card>

                <Card title="Fila">
                  <Stat label="Ativos" value={data.queue.active ?? 0} />
                  <Stat label="Aguardando" value={data.queue.waiting ?? 0} />
                  <Stat label="Concluídos" value={data.queue.completed ?? 0} />
                </Card>

                <Card title="Memória">
                  <Stat label="Episódica" value={data.memory.episodic ?? 0} />
                  <Stat label="Semântica" value={data.memory.semantic ?? 0} />
                  <Stat label="Total" value={data.memory.total ?? 0} color="var(--color-accent2)" />
                </Card>

                {data.retrieval_quality && (
                  <Card title="Qualidade de recuperação">
                    <Stat label="Score médio"
                      value={data.retrieval_quality.avg_score != null
                        ? data.retrieval_quality.avg_score.toFixed(2) : '—'} />
                    <Stat label="Hit rate"
                      value={data.retrieval_quality.hit_rate != null
                        ? `${(data.retrieval_quality.hit_rate * 100).toFixed(0)}%` : '—'} />
                  </Card>
                )}

                {data.contradictions && (
                  <Card title="Contradições">
                    <Stat label="Total" value={data.contradictions.stats.total} />
                    <Stat label="Não revisadas" value={data.contradictions.stats.unreviewed}
                      color={data.contradictions.stats.unreviewed > 0 ? 'var(--color-warn)' : 'var(--color-ok)'} />
                  </Card>
                )}

                <Card title="Saúde">
                  <Stat label="Status" value={data.health.status}
                    color={data.health.status === 'ok' ? 'var(--color-ok)' : 'var(--color-warn)'} />
                  <Stat label="Versão" value={data.health.version} />
                  <Stat label="Sessões" value={data.health.sessions?.length ?? 0} />
                </Card>
              </div>
            )}
          </QueryState>
        </div>
      </div>
    </div>
  )
}
