export interface SegmentedControlProps<T extends string> {
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  /** 묶음 이름 (스크린리더용) */
  ariaLabel: string
}

/** 2~4개 중 하나 고르기 (DejaFix 상단바 KST/UTC 전환) */
export function SegmentedControl<T extends string>({ options, value, onChange, ariaLabel }: SegmentedControlProps<T>) {
  return (
    <div className="seg" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? 'on' : undefined}
          aria-pressed={o.value === value} // DejaFix 대비 예외: 선택 상태를 스크린리더에 알림
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
