import { useRef, useState, type KeyboardEvent } from 'react'

interface Props {
  onSend: (text: string) => void
  disabled: boolean
}

export default function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  function submit() {
    const t = text.trim()
    if (!t || disabled) return
    onSend(t)
    setText('')
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function autoGrow(el: HTMLTextAreaElement) {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  return (
    <div
      className="border-t px-4 py-3"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg)' }}
    >
      <div
        className="flex items-end gap-2 rounded-xl border px-3 py-2"
        style={{ background: 'var(--color-surface)', borderColor: 'var(--color-border)' }}
      >
        <textarea
          ref={taRef}
          value={text}
          rows={1}
          placeholder="Escreva uma mensagem…  (Enter envia · Shift+Enter quebra linha)"
          onChange={e => {
            setText(e.target.value)
            autoGrow(e.target)
          }}
          onKeyDown={onKeyDown}
          className="flex-1 resize-none bg-transparent outline-none text-sm leading-relaxed"
          style={{ color: 'var(--color-text)', maxHeight: 200 }}
        />
        <button
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="flex-shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-opacity disabled:opacity-40"
          style={{ background: 'var(--color-accent)', color: '#fff' }}
        >
          {disabled ? '…' : 'Enviar'}
        </button>
      </div>
    </div>
  )
}
