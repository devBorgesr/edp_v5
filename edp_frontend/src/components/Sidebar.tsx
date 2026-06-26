import { NavLink } from 'react-router-dom'
import type { WsStatus } from '../hooks/useWebSocket'
import StatusDot from './StatusDot'

const nav = [
  { to: '/',          label: 'Chat',      icon: '◈', end: true  },
  { to: '/memory',    label: 'Memória',   icon: '⊡', end: false },
  { to: '/echo',      label: 'Câmara',    icon: '⚡', end: false },
  { to: '/dashboard', label: 'Dashboard', icon: '◎', end: false },
  { to: '/flags',     label: 'Flags',     icon: '⊞', end: false },
]

export default function Sidebar({ status }: { status: WsStatus }) {
  return (
    <aside
      className="w-60 flex-shrink-0 flex flex-col border-r"
      style={{ background: 'var(--color-surface)', borderColor: 'var(--color-border)' }}
    >
      {/* Branding */}
      <div className="px-5 py-4 border-b" style={{ borderColor: 'var(--color-border)' }}>
        <p
          className="text-xs font-bold tracking-[0.2em] uppercase"
          style={{ color: 'var(--color-accent)' }}
        >
          EDP v5
        </p>
        <p className="text-xs mt-0.5" style={{ color: 'var(--color-muted)' }}>
          Episodic-Driven Pipeline
        </p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
        {nav.map(({ to, label, icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors"
            style={({ isActive }) => ({
              background: isActive ? 'rgba(124, 58, 237, 0.2)' : 'transparent',
              color: isActive ? '#fff' : 'var(--color-muted)',
              fontWeight: isActive ? '500' : '400',
            })}
          >
            <span className="w-4 text-center flex-shrink-0 opacity-80">{icon}</span>
            {label}
          </NavLink>
        ))}
      </nav>

      {/* WS status footer */}
      <div className="px-4 py-3 border-t" style={{ borderColor: 'var(--color-border)' }}>
        <StatusDot status={status} />
      </div>
    </aside>
  )
}
