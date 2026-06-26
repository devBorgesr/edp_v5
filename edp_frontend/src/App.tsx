import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ChatPage from './pages/ChatPage'
import MemoryPage from './pages/MemoryPage'
import EchoPage from './pages/EchoPage'
import DashboardPage from './pages/DashboardPage'
import FlagsPage from './pages/FlagsPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ChatPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="echo" element={<EchoPage />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="flags" element={<FlagsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
