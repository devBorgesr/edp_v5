import type { StageInfo } from '../../store/chat'

export default function StageBar({ stage }: { stage: StageInfo }) {
  return (
    <div
      className="flex items-center gap-2 px-4 py-1.5 text-xs animate-fadein"
      style={{ color: 'var(--color-muted)', background: 'var(--color-panel)' }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full pulse-dot flex-shrink-0"
        style={{ background: 'var(--color-accent)' }}
      />
      {stage.stage}
    </div>
  )
}
