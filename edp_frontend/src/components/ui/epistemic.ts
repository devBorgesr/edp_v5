// Mapeamento de status epistêmico → cores do tema.
export function epistemicColor(status?: string): { color: string; bg: string } {
  switch (status) {
    case 'verified':     return { color: 'var(--color-ok)',     bg: 'rgba(34,197,94,0.12)' }
    case 'hypothesis':   return { color: 'var(--color-accent2)', bg: 'rgba(6,182,212,0.12)' }
    case 'stale':        return { color: 'var(--color-muted)',  bg: 'rgba(100,116,139,0.12)' }
    case 'contradicted': return { color: 'var(--color-err)',    bg: 'rgba(239,68,68,0.12)' }
    case 'quarantined':  return { color: 'var(--color-warn)',   bg: 'rgba(245,158,11,0.12)' }
    default:             return { color: 'var(--color-muted)',  bg: 'var(--color-panel)' }
  }
}
