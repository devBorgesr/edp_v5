interface Props {
  label: string
  color?: string
  bg?: string
}

export default function Badge({ label, color = 'var(--color-muted)', bg }: Props) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap"
      style={{ color, background: bg ?? 'var(--color-panel)' }}
    >
      {label}
    </span>
  )
}
