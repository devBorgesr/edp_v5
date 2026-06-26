// Cliente WebSocket para o EDP v5.
// Singleton por session_id. Reconexão automática com backoff exponencial.

import type { WsMessage } from '../types/api'

export type WsListener = (msg: WsMessage) => void
export type WsStatusListener = (status: 'connecting' | 'open' | 'closed' | 'error') => void

const BACKOFF = [1000, 2000, 4000, 8000, 16000]

class EdpWebSocket {
  private ws: WebSocket | null = null
  private sessionId: string
  private listeners: Set<WsListener> = new Set()
  private statusListeners: Set<WsStatusListener> = new Set()
  private retryCount = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private intentionallyClosed = false

  constructor(sessionId: string) {
    this.sessionId = sessionId
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    this.intentionallyClosed = false
    this._connect()
  }

  private _connect() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const url = `${proto}://${host}/ws/chat/${this.sessionId}`

    this._emitStatus('connecting')
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.retryCount = 0
      this._emitStatus('open')
    }

    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsMessage
        if (msg.type === 'heartbeat') return
        this.listeners.forEach(l => l(msg))
      } catch {
        // json parse error — ignore
      }
    }

    this.ws.onerror = () => {
      this._emitStatus('error')
    }

    this.ws.onclose = () => {
      this._emitStatus('closed')
      if (!this.intentionallyClosed) {
        const delay = BACKOFF[Math.min(this.retryCount, BACKOFF.length - 1)]
        this.retryCount++
        this.retryTimer = setTimeout(() => this._connect(), delay)
      }
    }
  }

  send(message: string) {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket not connected')
    }
    this.ws.send(JSON.stringify({ message }))
  }

  disconnect() {
    this.intentionallyClosed = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.ws?.close()
    this.ws = null
  }

  addListener(fn: WsListener) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  addStatusListener(fn: WsStatusListener) {
    this.statusListeners.add(fn)
    return () => this.statusListeners.delete(fn)
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED
  }

  private _emitStatus(s: Parameters<WsStatusListener>[0]) {
    this.statusListeners.forEach(l => l(s))
  }
}

// Singleton por session
const instances = new Map<string, EdpWebSocket>()

export function getWs(sessionId = 'default'): EdpWebSocket {
  if (!instances.has(sessionId)) {
    instances.set(sessionId, new EdpWebSocket(sessionId))
  }
  return instances.get(sessionId)!
}
