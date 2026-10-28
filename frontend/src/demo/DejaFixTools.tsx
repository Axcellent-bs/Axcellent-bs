// DejaFix 화면(iframe)의 상단바를 숨기고, 그 안의 도구를 AXcellent 바깥 상단바로 꺼낸다.
// 가장 급한 장애 · 시간대(KST/UTC) · AI 어시스턴트. 상태는 DejaFix 안에 있으니 숨긴 원래 버튼을 대신 누르고, 표시는 주기적으로 읽어 온다.
import { useEffect, useState } from 'react'
import { IconBolt } from '@/components/icons'

/** DejaFixFrame.tsx의 iframe 안 문서 (같은 출처라 읽을 수 있다) */
const doc = () => document.querySelector<HTMLIFrameElement>('iframe[data-dejafix]')?.contentDocument ?? null
const press = (selector: string) => doc()?.querySelector<HTMLElement>(selector)?.click()

type View = { urgent: string; hot: boolean; tz: string; panel: boolean }

/** compact: 상단 탭이 같은 줄에 있는 현재 UI에서는 급한 장애 글을 아주 넓은 화면에서만 보인다 */
export function DejaFixTools({ compact = false }: { compact?: boolean }) {
  const [v, setV] = useState<View | null>(null)

  useEffect(() => {
    const read = () => {
      const d = doc()
      const bar = d?.querySelector<HTMLElement>('.topbar')
      if (!d || !bar) return setV(null)
      const urgent = bar.querySelector<HTMLElement>('.urgentbar')
      setV({
        // 조각(제목·호스트 등)이 붙어 있지 않게 자식마다 띄어 잇는다
        urgent: urgent ? [...urgent.children].map((c) => c.textContent?.trim()).filter(Boolean).join(' ') || (urgent.textContent ?? '').trim() : '',
        hot: !!urgent?.classList.contains('hot'),
        tz: bar.querySelector('.seg button.on')?.textContent ?? 'KST',
        panel: !!d.querySelector('.main.panel-open'),
      })
    }
    read()
    const t = setInterval(read, 500)
    return () => clearInterval(t)
  }, [])

  if (!v) return null
  return (
    <div className="flex min-w-0 items-center gap-3">
      {v.urgent && (
        <button type="button" onClick={() => press('.topbar .urgentbar')} title={v.urgent}
          className={`hidden min-w-0 max-w-[420px] items-center gap-1.5 truncate text-sm ${compact ? '2xl:flex' : 'xl:flex'} ${v.hot ? 'text-red-700' : 'text-ink-700'}`}>
          <span className="truncate">{v.urgent}</span>
        </button>
      )}
      <div role="group" aria-label="시간대" className="flex shrink-0 bg-ink-100 p-0.5 text-xs">
        {['KST', 'UTC'].map((z) => (
          <button key={z} type="button" aria-pressed={v.tz === z} onClick={() => press(`.topbar .seg button:nth-child(${z === 'KST' ? 1 : 2})`)}
            className={`px-2.5 py-1 font-semibold ${v.tz === z ? 'bg-brand-600 text-white' : 'text-ink-700'}`}>
            {z}
          </button>
        ))}
      </div>
      <button type="button" onClick={() => press('.topbar .btn.dark')} aria-pressed={v.panel}
        className="btn-primary shrink-0 gap-1.5">
        <IconBolt size={14} />
        AI 어시스턴트
      </button>
    </div>
  )
}
