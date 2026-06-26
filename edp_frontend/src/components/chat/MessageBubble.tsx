import type { ChatMessage } from '../../store/chat'
import Markdown from '../Markdown'
import CamaraPanel from './CamaraPanel'
import LineagePanel from './LineagePanel'

export default function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end animate-fadein">
        <div
          className="max-w-[78%] rounded-2xl rounded-br-sm px-4 py-2.5 whitespace-pre-wrap"
          style={{ background: 'var(--color-accent)', color: '#fff' }}
        >
          {msg.content}
        </div>
      </div>
    )
  }

  const showCursor = msg.streaming && !msg.content
  const hasCamara = !!msg.camara?.iniciada

  return (
    <div className="flex justify-start animate-fadein">
      <div className="max-w-[85%] w-full">
        <div
          className="rounded-2xl rounded-bl-sm px-4 py-3 border"
          style={{ background: 'var(--color-surface)', borderColor: 'var(--color-border)' }}
        >
          {showCursor ? (
            <span className="streaming-cursor" style={{ color: 'var(--color-muted)' }} />
          ) : (
            <div className={msg.streaming ? 'streaming-cursor' : ''}>
              <Markdown>{msg.content}</Markdown>
            </div>
          )}
        </div>

        {hasCamara && <CamaraPanel camara={msg.camara!} />}
        {msg.lineage && <LineagePanel lineage={msg.lineage} />}

        {/* Footer meta — apenas quando finalizado */}
        {!msg.streaming && (msg.modelUsed || msg.memoryHits != null) && (
          <div
            className="flex items-center gap-3 mt-1.5 px-1 text-[10px]"
            style={{ color: 'var(--color-muted)' }}
          >
            {msg.modelUsed && <span>{msg.modelUsed}</span>}
            {msg.memoryHits != null && <span>{msg.memoryHits} hits de memória</span>}
            {msg.compressionPct != null && <span>{msg.compressionPct.toFixed(0)}% compressão</span>}
          </div>
        )}
      </div>
    </div>
  )
}
