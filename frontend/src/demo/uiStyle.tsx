// 화면 스타일 토글: 지금 업무 시스템 모양(현재)과 DejaFix common-frontend 모양(공통)을 바꿔 본다.
// <html data-ui="common">이면 styles.css가 색·모서리를 common-frontend 토큰으로 바꾼다. 고른 값은 브라우저에만 기억한다.
import { useEffect, useState } from 'react'

export type UiStyle = 'current' | 'common'
const KEY = 'axcellent-demo-ui'

const read = (): UiStyle => (document.documentElement.dataset.ui === 'common' ? 'common' : 'current')

export function applyUiStyle(s: UiStyle) {
  document.documentElement.dataset.ui = s
  try {
    localStorage.setItem(KEY, s)
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 현재 모양으로 돌아간다 */
  }
}

/** index.html이 첫 화면 전에 넣은 data-ui 값을 따라간다 */
export function useUiStyle(): UiStyle {
  const [ui, setUi] = useState(read)
  useEffect(() => {
    const ob = new MutationObserver(() => setUi(read()))
    ob.observe(document.documentElement, { attributes: true, attributeFilter: ['data-ui'] })
    return () => ob.disconnect()
  }, [])
  return ui
}

const OPTIONS: { value: UiStyle; label: string; title: string }[] = [
  { value: 'current', label: '현재', title: '현재 UI' },
  { value: 'common', label: '공통', title: 'common-frontend UI' },
]

export function UiStyleToggle({ horizontal = false }: { horizontal?: boolean }) {
  const ui = useUiStyle()
  return (
    <div role="radiogroup" aria-label="화면 스타일" className={`flex rounded-full bg-ink-100 p-0.5 text-[11px] ${horizontal ? "" : "flex-col"}`}>
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={ui === o.value}
          title={o.title}
          onClick={() => applyUiStyle(o.value)}
          className={`flex h-7 w-10 items-center justify-center rounded-full font-medium transition ${
            ui === o.value ? 'bg-surface text-ink-900 shadow-sm' : 'text-ink-500 hover:text-ink-900'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
