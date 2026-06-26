import { useEffect, useRef } from 'react'
import { useChat } from '../store/chat'
import MessageBubble from '../components/chat/MessageBubble'
import Composer from '../components/chat/Composer'
import StageBar from '../components/chat/StageBar'

export default function ChatPage() {
  const messages = useChat(s => s.messages)
  const stage = useChat(s => s.stage)
  const sending = useChat(s => s.sending)
  const sendMessage = useChat(s => s.sendMessage)
  const clearMessages = useChat(s => s.clearMessages)

  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll para o fim quando há mensagens novas / streaming,
  // mas só se o usuário já estiver perto do fim.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, stage])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <header
        className="flex items-center justify-between px-5 py-3 border-b flex-shrink-0"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
      >
        <div>
          <h1 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>Chat</h1>
          <p className="text-xs" style={{ color: 'var(--color-muted)' }}>
            sessão <code style={{ color: 'var(--color-accent2)' }}>default</code>
          </p>
        </div>
        {messages.length > 0 && (
          <button
            onClick={clearMessages}
            className="text-xs px-2.5 py-1 rounded-md border transition-colors hover:opacity-80"
            style={{ borderColor: 'var(--color-border)', color: 'var(--color-muted)' }}
          >
            Limpar
          </button>
        )}
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-5 space-y-4">
          {messages.length === 0 ? (
            <div className="text-center pt-20" style={{ color: 'var(--color-muted)' }}>
              <p className="text-3xl mb-3" style={{ color: 'var(--color-accent)' }}>◈</p>
              <p className="text-sm">Comece uma conversa com o EDP.</p>
              <p className="text-xs mt-1">
                Memória, câmara de eco e lineage aparecem junto de cada resposta.
              </p>
            </div>
          ) : (
            messages.map(m => <MessageBubble key={m.id} msg={m} />)
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Stage indicator */}
      {stage && <StageBar stage={stage} />}

      {/* Composer */}
      <Composer onSend={sendMessage} disabled={sending} />
    </div>
  )
}
