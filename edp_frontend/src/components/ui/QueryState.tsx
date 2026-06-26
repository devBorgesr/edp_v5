import type { ReactNode } from 'react'

interface Props {
  isLoading: boolean
  error: unknown
  isEmpty?: boolean
  emptyLabel?: string
  children: ReactNode
}

export default function QueryState({ isLoading, error, isEmpty, emptyLabel, children }: Props) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-sm" style={{ color: 'var(--color-muted)' }}>
        <span className="w-2 h-2 rounded-full pulse-dot mr-2" style={{ background: 'var(--color-accent)' }} />
        Carregando…
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-sm" style={{ color: 'var(--color-err)' }}>
        <p>Erro ao carregar.</p>
        <p className="text-xs mt-1" style={{ color: 'var(--color-muted)' }}>
          {error instanceof Error ? error.message : String(error)}
        </p>
        <p className="text-xs mt-2" style={{ color: 'var(--color-muted)' }}>
          O backend está rodando em <code>:8000</code>?
        </p>
      </div>
    )
  }
  if (isEmpty) {
    return (
      <div className="flex items-center justify-center py-20 text-sm" style={{ color: 'var(--color-muted)' }}>
        {emptyLabel ?? 'Nada por aqui ainda.'}
      </div>
    )
  }
  return <>{children}</>
}
