import type { ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: string
  right?: ReactNode
}

export default function PageHeader({ title, subtitle, right }: Props) {
  return (
    <header
      className="flex items-center justify-between px-5 py-3 border-b flex-shrink-0"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <div>
        <h1 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>{title}</h1>
        {subtitle && (
          <p className="text-xs" style={{ color: 'var(--color-muted)' }}>{subtitle}</p>
        )}
      </div>
      {right}
    </header>
  )
}
