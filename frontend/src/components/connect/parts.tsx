import type { ReactNode } from 'react'

// 연결 화면들(회사 연결 /system/connect, 연결 /connections)이 같이 쓰는 계층 부품.
// 계층: 페이지 제목(한 줄 요약) › 구역 제목(+상태) › 카드(핵심 값은 크게, 보조 사실은 흐리게) › 도움말(펼침).
// 상태는 말로 쓴다(연결됨·확인 전·끊김). 색은 같은 뜻에 같은 색만 쓴다.

export type Tone = 'ok' | 'wait' | 'bad' | 'off'
export const TONE_CHIP: Record<Tone, string> = {
  ok: 'bg-emerald-50 text-emerald-700',
  wait: 'bg-amber-50 text-amber-700',
  bad: 'bg-red-50 text-red-700',
  off: 'bg-ink-100 text-ink-700',
}
export const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-emerald-700',
  wait: 'bg-amber-700',
  bad: 'bg-red-700',
  off: 'bg-ink-300',
}

export function StatusChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`chip ${TONE_CHIP[tone]}`}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 ${TONE_DOT[tone]}`} />
      {children}
    </span>
  )
}

/** 페이지 맨 위: 어디(위치) · 제목 · 이 화면에서 하는 일 한 줄 */
export function PageHead({ where, title, children }: { where?: string; title: string; children?: ReactNode }) {
  return (
    <header className="max-w-3xl">
      {where && <p className="text-sm text-ink-500">{where}</p>}
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {children && <p className="mt-1.5 text-sm leading-6 text-ink-700">{children}</p>}
    </header>
  )
}

/** 페이지 맨 위 요약 칸: 이 화면의 핵심 숫자·상태를 한 줄에. 칸마다 점(상태)·이름·값(크게)·한 줄 설명 */
export function Tiles({ items, label }: { items: { name: string; value: ReactNode; tone?: Tone; note?: string }[]; label: string }) {
  const cols = items.length >= 4 ? 'sm:grid-cols-2 lg:grid-cols-4' : items.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2'
  return (
    <ul className={`grid gap-px border border-ink-100 bg-ink-100 ${cols}`} aria-label={label}>
      {items.map((t) => (
        <li key={t.name} className="bg-surface px-5 py-4">
          <p className="flex items-center gap-2 text-xs text-ink-500">
            {t.tone && <span aria-hidden="true" className={`h-2 w-2 ${TONE_DOT[t.tone]}`} />}
            {t.name}
          </p>
          <p className="mt-1.5 truncate text-lg font-semibold">{t.value}</p>
          <p className="mt-0.5 truncate text-xs text-ink-500" title={t.note}>{t.note || ' '}</p>
        </li>
      ))}
    </ul>
  )
}

/** 페이지 안의 탭: 밑줄로 고른 탭을 보여 준다(위계상 페이지 제목 아래, 구역 제목 위) */
export function Tabs<T extends string>({ items, value, onPick, label }: { items: readonly { id: T; label: string; count?: number }[]; value: T; onPick: (id: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-x-1 border-b border-ink-100">
      {items.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} onClick={() => onPick(t.id)}
          className={`-mb-px border-b-2 px-4 py-2.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${value === t.id ? 'border-brand-600 font-semibold text-ink-900' : 'border-transparent text-ink-500 hover:text-ink-900'}`}>
          {t.label}
          {t.count != null && <span className="ml-1.5 text-xs font-normal text-ink-500">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

/** 구역 제목: 왼쪽 제목·설명, 오른쪽 상태나 주 동작 */
export function SectionHead({ title, desc, right, as: H = 'h2' }: { title: string; desc?: ReactNode; right?: ReactNode; as?: 'h2' | 'h3' }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div className="min-w-0 max-w-2xl">
        <H className={H === 'h2' ? 'text-lg font-semibold' : 'text-[15px] font-semibold text-ink-900'}>{title}</H>
        {desc && <p className="mt-0.5 text-sm leading-6 text-ink-500">{desc}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  )
}

/** 보조 사실 목록: 이름은 흐리게, 값은 진하게. 값이 없으면 "—" */
export function Facts({ items, cols = 3 }: { items: [ReactNode, ReactNode][]; cols?: 2 | 3 | 4 }) {
  const grid = cols === 2 ? 'sm:grid-cols-2' : cols === 4 ? 'sm:grid-cols-2 lg:grid-cols-4' : 'sm:grid-cols-2 lg:grid-cols-3'
  return (
    <dl className={`grid gap-x-6 gap-y-3 text-sm ${grid}`}>
      {items.map(([k, v], i) => (
        <div key={i} className="min-w-0">
          <dt className="text-xs text-ink-500">{k}</dt>
          <dd className="mt-0.5 break-words text-ink-900">{v ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

/** 카드 맨 아래 따로 떼어 둔 되돌리기 어려운 동작 (끊기·지우기) */
export function DangerRow({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 bg-ink-50/60 px-6 py-3">
      <p className="text-xs text-ink-500">{note}</p>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

/** 알림 줄: 막힌 이유·주의를 한 곳에. 말투는 무엇이 왜 막혔고 어떻게 푸는지 */
export function Notice({ tone = 'wait', children }: { tone?: 'wait' | 'bad' | 'info'; children: ReactNode }) {
  const cls = tone === 'bad' ? 'border-red-700 bg-red-50 text-red-800' : tone === 'info' ? 'border-ink-300 bg-ink-50 text-ink-700' : 'border-amber-700 bg-amber-50 text-amber-800'
  return <p role={tone === 'bad' ? 'alert' : 'status'} className={`border-l-2 px-4 py-2.5 text-sm leading-6 ${cls}`}>{children}</p>
}

/** 펼쳐 보는 설명. 화면에는 제목 한 줄만 */
export function More({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group text-sm">
      <summary className="cursor-pointer list-none select-none text-xs font-medium text-ink-500 hover:text-ink-900 [&::-webkit-details-marker]:hidden">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden="true">›</span> {summary}
      </summary>
      <div className="mt-2 space-y-2 pl-3 text-xs leading-5 text-ink-500">{children}</div>
    </details>
  )
}
