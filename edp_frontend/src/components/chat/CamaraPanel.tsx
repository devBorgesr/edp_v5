import { useState } from 'react'
import type { ChatMessage } from '../../store/chat'
import type { CheckInfo } from '../../types/api'

const CHECK_LABELS: Record<string, string> = {
  confabulacao:        'Confabulação',
  inflacao_avaliativa: 'Inflação avaliativa',
  condescendencia:     'Condescendência',
  projecao_sem_dado:   'Projeção sem dado',
  perda_de_fio:        'Perda de fio',
  completude_forcada:  'Completude forçada',
  estruturacao_imposta:'Estruturação imposta',
}

function vencedorLabel(v: string): string {
  if (v === 'A') return 'Original (A)'
  if (v === 'B') return 'Reformulado (B)'
  return 'Equivalentes'
}

export default function CamaraPanel({ camara }: { camara: NonNullable<ChatMessage['camara']> }) {
  const [open, setOpen] = useState(false)
  const { iniciada, faseB, resultado } = camara
  if (!iniciada) return null

  const checks = faseB?.checks ?? {}
  const checkEntries = Object.entries(checks) as [string, CheckInfo][]
  const passCount = checkEntries.filter(([, c]) => c.verdict === 'PASS').length
  const total = checkEntries.length

  return (
    <div
      className="mt-2 rounded-lg border text-xs animate-fadein"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-panel)' }}
    >
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2">
          <span style={{ color: 'var(--color-warn)' }}>⚡ Câmara de Eco</span>
          <span style={{ color: 'var(--color-muted)' }}>
            {iniciada.modelo_A} ⇄ {iniciada.modelo_B}
          </span>
          {resultado && (
            <span
              className="px-1.5 py-0.5 rounded"
              style={{
                background: 'rgba(124,58,237,0.18)',
                color: '#c4b5fd',
              }}
            >
              {vencedorLabel(resultado.vencedor)}
            </span>
          )}
          {total > 0 && (
            <span style={{ color: passCount === total ? 'var(--color-ok)' : 'var(--color-warn)' }}>
              {passCount}/{total} checks
            </span>
          )}
        </span>
        <span style={{ color: 'var(--color-muted)' }}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t" style={{ borderColor: 'var(--color-border)' }}>
          {/* Fase A → B status */}
          <div className="pt-2 flex flex-wrap gap-x-4 gap-y-1" style={{ color: 'var(--color-muted)' }}>
            {resultado && (
              <>
                <span>Concordância: <b style={{ color: 'var(--color-text)' }}>{(resultado.concordancia * 100).toFixed(0)}%</b></span>
                {resultado.score_A && (
                  <span>
                    Score A: <b style={{ color: 'var(--color-text)' }}>
                      {resultado.score_A.score}/{resultado.score_A.max_score}
                    </b>
                  </span>
                )}
              </>
            )}
            {faseB && <span>Latência B: {faseB.latencia_ms_b} ms</span>}
            {faseB?.tem_reformulacao && (
              <span style={{ color: 'var(--color-accent2)' }}>contém reformulação</span>
            )}
          </div>

          {/* Checks grid */}
          {checkEntries.length > 0 ? (
            <div className="space-y-1">
              {checkEntries.map(([key, check]) => (
                <div key={key} className="flex items-start gap-2">
                  <span
                    className="mt-0.5 flex-shrink-0 font-bold"
                    style={{ color: check.verdict === 'PASS' ? 'var(--color-ok)' : 'var(--color-err)' }}
                  >
                    {check.verdict === 'PASS' ? '✓' : '✗'}
                  </span>
                  <span className="flex-shrink-0 w-36" style={{ color: 'var(--color-text)' }}>
                    {CHECK_LABELS[key] ?? key}
                  </span>
                  <span style={{ color: 'var(--color-muted)' }}>{check.justificativa}</span>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ color: 'var(--color-muted)' }}>Avaliação em curso…</p>
          )}
        </div>
      )}
    </div>
  )
}
