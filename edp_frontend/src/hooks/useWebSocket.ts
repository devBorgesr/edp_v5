import { useEffect, useRef, useState } from 'react'
import { getWs } from '../api/ws'
import type { WsMessage, CamaraChecks } from '../types/api'

// Re-export para acesso fácil
export type { CamaraChecks }

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

// Tipos importados inline para evitar dependência circular
import type {
  WsCamaraIniciada,
  WsCamaraFaseBCompleta,
  WsCamaraResultado,
  WsLineage,
} from '../types/api'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  id: string
  streaming?: boolean
  // Dados extras da resposta
  lineage?: WsLineage
  camara?: {
    iniciada?: WsCamaraIniciada
    faseB?: WsCamaraFaseBCompleta
    resultado?: WsCamaraResultado
  }
  modelUsed?: string
  memoryHits?: number
  compressionPct?: number
}

export interface StageInfo {
  stage: string
  model?: string
  memoryHits?: number
  routerBadge?: string
}

export function useWebSocket(sessionId = 'default') {
  const [status, setStatus] = useState<WsStatus>('closed')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [stage, setStage] = useState<StageInfo | null>(null)
  const [sending, setSending] = useState(false)

  const wsRef = useRef(getWs(sessionId))
  const currentMsgId = useRef<string | null>(null)
  const camaraState = useRef<ChatMessage['camara']>({})

  useEffect(() => {
    const ws = wsRef.current
    ws.connect()

    const unsub = ws.addListener(handleMessage)
    const unsubStatus = ws.addStatusListener(setStatus)

    return () => {
      unsub()
      unsubStatus()
    }
  }, [sessionId])

  function handleMessage(msg: WsMessage) {
    switch (msg.type) {
      case 'start': {
        const id = crypto.randomUUID()
        currentMsgId.current = id
        camaraState.current = {}
        setStage({ stage: 'Pipeline...' })
        setSending(true)
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: '', id, streaming: true },
        ])
        break
      }

      case 'pipeline_done':
        setStage(prev => ({
          ...prev,
          stage: `Memória: ${msg.memory_hits} hits • ${msg.compression_pct.toFixed(0)}% compressão`,
          memoryHits: msg.memory_hits,
        }))
        break

      case 'router_decision':
        setStage(prev => ({ ...prev, stage: `Modelo: ${msg.badge}`, routerBadge: msg.badge }))
        break

      case 'llm_start':
        setStage(prev => ({ ...prev, stage: `Gerando com ${msg.model}…`, model: msg.model }))
        break

      case 'chunk':
        if (!currentMsgId.current) break
        setMessages(prev => prev.map(m =>
          m.id === currentMsgId.current
            ? { ...m, content: m.content + msg.text }
            : m
        ))
        break

      case 'camara_iniciada':
        setStage(prev => ({ ...prev, stage: `⚡ Câmara: ${msg.modelo_B} avaliando…` }))
        camaraState.current = { ...camaraState.current, iniciada: msg }
        _updateCurrentMsg({ camara: { ...camaraState.current } })
        break

      case 'camara_fase_b_completa':
        setStage(prev => ({ ...prev, stage: '⚡ Câmara: síntese em curso…' }))
        camaraState.current = { ...camaraState.current, faseB: msg }
        _updateCurrentMsg({ camara: { ...camaraState.current } })
        break

      case 'camara_resultado':
        setStage(prev => ({ ...prev, stage: `⚡ Câmara: vencedor ${msg.vencedor}` }))
        camaraState.current = { ...camaraState.current, resultado: msg }
        _updateCurrentMsg({ camara: { ...camaraState.current } })
        break

      case 'lineage':
        _updateCurrentMsg({ lineage: msg })
        break

      case 'done':
        if (currentMsgId.current) {
          setMessages(prev => prev.map(m =>
            m.id === currentMsgId.current
              ? {
                  ...m,
                  streaming: false,
                  memoryHits: msg.memory_hits,
                  compressionPct: msg.compression_pct,
                }
              : m
          ))
        }
        currentMsgId.current = null
        setSending(false)
        setStage(null)
        break

      case 'warn':
        setStage({ stage: `⚠ ${msg.error}` })
        break

      case 'error':
        setStage({ stage: `Erro: ${msg.error}` })
        setSending(false)
        break
    }
  }

  function _updateCurrentMsg(patch: Partial<ChatMessage>) {
    if (!currentMsgId.current) return
    setMessages(prev => prev.map(m =>
      m.id === currentMsgId.current ? { ...m, ...patch } : m
    ))
  }

  function sendMessage(text: string) {
    const trimmed = text.trim()
    if (!trimmed || sending) return

    setMessages(prev => [
      ...prev,
      { role: 'user', content: trimmed, id: crypto.randomUUID() },
    ])

    try {
      wsRef.current.send(trimmed)
    } catch (e) {
      setStage({ stage: `Erro de conexão: ${String(e)}` })
    }
  }

  function clearMessages() {
    setMessages([])
    currentMsgId.current = null
    setSending(false)
    setStage(null)
  }

  return { status, messages, stage, sending, sendMessage, clearMessages }
}
