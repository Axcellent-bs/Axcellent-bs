import { useRef } from 'react'
import { useHotkey } from '../hooks/useHotkey'
import { Icon } from './Icon'
import { Kbd } from './Kbd'

export interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  /** Enter를 누르면 앞뒤 공백을 뺀 검색어로 부른다 (빈 값이면 부르지 않음) */
  onSubmit?: (query: string) => void
  placeholder?: string
  /** Ctrl/⌘+K로 포커스. 한 화면에 하나만 켠다 */
  hotkey?: boolean
  ariaLabel?: string
}

/** 상단바 검색칸 */
export function SearchBox({
  value,
  onChange,
  onSubmit,
  placeholder = '검색',
  hotkey = true,
  ariaLabel = '검색',
}: SearchBoxProps) {
  const ref = useRef<HTMLInputElement>(null)
  useHotkey('k', () => ref.current?.focus(), hotkey)
  return (
    <label className="search">
      <Icon name="search" size={16} />
      <input
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // DejaFix 대비 예외: 한글 입력(조합) 중 Enter는 글자 확정이므로 검색하지 않는다
          if (e.key === 'Enter' && value.trim() && !e.nativeEvent.isComposing) onSubmit?.(value.trim())
        }}
        placeholder={placeholder}
        aria-label={ariaLabel}
      />
      {hotkey && <Kbd>Ctrl K</Kbd>}
    </label>
  )
}
