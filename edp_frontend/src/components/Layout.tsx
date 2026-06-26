import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import { useWebSocket } from '../hooks/useWebSocket'

export default function Layout() {
  const { status } = useWebSocket()

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--color-bg)' }}>
      <Sidebar status={status} />
      <main className="flex-1 overflow-hidden flex flex-col">
        <Outlet />
      </main>
    </div>
  )
}
