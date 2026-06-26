// Store global do chat — fonte única de verdade para estado do WebSocket.
// Um único listener no socket singleton; Layout e ChatPage leem daqui.

import { create } from 'zustand'
import { getWs } from '../api/ws'
import type {
  WsMessage,
  WsCamaraIniciada,
  WsCamaraFaseBCompleta,
  WsCamaraResultado,
  WsLineage,
} from '../types/api'

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  id: string
  streaming?: boolean
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

interface ChatState {
  status: WsStatus
  messages: ChatMessage[]
  stage: StageInfo | null
  sending: boolean
  sendMessage: (text: string) => void
  clearMessages: () => void
}

// Refs mutáveis fora do estado reativo (não disparam render)
let currentMsgId: string | null = null
let camaraState: ChatMessage['camara'] = {}
let initialized = false

const ws = getWs('default')

export const useChat = create<ChatState>((set, get) => {
  function patchCurrent(patch: Partial<ChatMessage>) {
    if (!currentMsgId) return
    set(s => ({
      messages: s.messages.map(m => (m.id === currentMsgId ? { ...m, ...patch } : m)),
    }))
  }

  function handle(msg: WsMessage) {
    switch (msg.type) {
      case 'start': {
        const id = crypto.randomUUID()
        currentMsgId = id
        camaraState = {}
        set(s => ({
          stage: { stage: 'Pipeline…' },
          sending: true,
          messages: [...s.messages, { role: 'assistant', content: '', id, streaming: true }],
        }))
        break
      }

      case 'pipeline_done':
        set(s => ({
          stage: {
            ...s.stage,
            stage: `Memória: ${msg.memory_hits} hits • ${msg.compression_pct.toFixed(0)}% compressão`,
            memoryHits: msg.memory_hits,
          },
        }))
        break

      case 'router_decision':
        set(s => ({ stage: { ...s.stage, stage: `Modelo: ${msg.badge}`, routerBadge: msg.badge } }))
        break

      case 'llm_start':
        set(s => ({ stage: { ...s.stage, stage: `Gerando com ${msg.model}…`, model: msg.model } }))
        break

      case 'chunk':
        if (!currentMsgId) break
        set(s => ({
          messages: s.messages.map(m =>
            m.id === currentMsgId ? { ...m, content: m.content + msg.text } : m,
          ),
        }))
        break

      case 'camara_iniciada':
        set(s => ({ stage: { ...s.stage, stage: `⚡ Câmara: ${msg.modelo_B} avaliando…` } }))
        camaraState = { ...camaraState, iniciada: msg }
        patchCurrent({ camara: { ...camaraState } })
        break

      case 'camara_fase_b_completa':
        set(s => ({ stage: { ...s.stage, stage: '⚡ Câmara: síntese em curso…' } }))
        camaraState = { ...camaraState, faseB: msg }
        patchCurrent({ camara: { ...camaraState } })
        break

      case 'camara_resultado':
        set(s => ({ stage: { ...s.stage, stage: `⚡ Câmara: vencedor ${msg.vencedor}` } }))
        camaraState = { ...camaraState, resultado: msg }
        patchCurrent({ camara: { ...camaraState }, modelUsed: msg.modelo_A })
        break

      case 'lineage':
        patchCurrent({ lineage: msg, modelUsed: msg.model_used })
        break

      case 'done':
        if (currentMsgId) {
          set(s => ({
            messages: s.messages.map(m =>
              m.id === currentMsgId
                ? {
                    ...m,
                    streaming: false,
                    memoryHits: msg.memory_hits,
                    compressionPct: msg.compression_pct,
                  }
                : m,
            ),
          }))
        }
        currentMsgId = null
        set({ sending: false, stage: null })
        break

      case 'warn':
        set(s => ({ stage: { ...s.stage, stage: `⚠ ${msg.error}` } }))
        break

      case 'error':
        set({ stage: { stage: `Erro: ${msg.error}` }, sending: false })
        currentMsgId = null
        break
    }
  }

  // Inicialização idempotente: conecta e registra listeners uma única vez.
  if (!initialized) {
    initialized = true
    ws.addStatusListener(s => set({ status: s }))
    ws.addListener(handle)
    ws.connect()
  }

  return {
    status: 'closed',
    messages: [],
    stage: null,
    sending: false,

    sendMessage(text: string) {
      const trimmed = text.trim()
      if (!trimmed || get().sending) return
      set(s => ({
        messages: [...s.messages, { role: 'user', content: trimmed, id: crypto.randomUUID() }],
      }))
      try {
        ws.send(trimmed)
      } catch (e) {
        set({ stage: { stage: `Erro de conexão: ${String(e)}` } })
      }
    },

    clearMessages() {
      currentMsgId = null
      camaraState = {}
      set({ messages: [], sending: false, stage: null })
    },
  }
})
