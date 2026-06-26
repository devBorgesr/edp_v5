import type { WsStatus } from '../store/chat'

const labels: Record<WsStatus, string> = {
  open:       'Conectado',
  connecting: 'Conectando…',
  closed:     'Desconectado',
  error:      'Erro WS',
}

const colors: Record<WsStatus, string> = {
  open:       'var(--color-ok)',
  connecting: 'var(--color-warn)',
  closed:     'var(--color-muted)',
  error:      'var(--color-err)',
}

export default function StatusDot({ status }: { status: WsStatus }) {
  const color = colors[status]
  return (
    <span className="flex items-center gap-1.5 text-xs" style={{ color }}>
      <span
        className="w-2 h-2 rounded-full flex-shrink-0"
        style={{
          background: color,
          boxShadow: status === 'open' ? `0 0 6px ${color}` : 'none',
        }}
      />
      {labels[status]}
    </span>
  )
}
