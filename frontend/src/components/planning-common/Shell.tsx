// 기획(/planning)·개발(/dev) 화면이 같이 쓰는 틀. 둘 다 같은 planning 도메인 백엔드(기획 8101, 개발 8111)를 본다.
// 틀 = 제목·데이터 전환·서버 상태 + 메뉴 줄 + 가로탭 + 새로 받은 연동. 메뉴와 화면 내용은 각 페이지(pages/Planning.tsx, pages/Dev.tsx)가 정한다.
// 화면 안 이동은 경로(/planning|/dev/<메뉴>/<가로탭>/<항목>/<하위>)로 한다.
// 공통 화면 컴포넌트(@dejafix/common-frontend)의 스타일은 .planning-scope 안에만 적용된다 (postcss.config.js).
import '@dejafix/common-frontend/styles.css'
import '@/components/planning-common/planning.css'
import { useEffect, useReducer, useRef, useState, type Dispatch, type MouseEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { Chip, ToastOutlet, ToastProvider, useToast, type NavItem } from '@dejafix/common-frontend'
import { ACTION_ERROR_EVENT, useSharedStore, apiBase } from '@/lib/planning'
import { TODAY, initialState, reducer, type Action, type State } from '@/lib/planningData'
import { Dep, Point } from '@/components/planning-common/parts'
import { convoHref, go, setPlanningNav, toPath } from '@/components/planning-common/links'

type IconName = NonNullable<NavItem['icon']>
// hidden: 메뉴에는 안 보이지만 주소로는 열린다 (소통은 부서 공용 탭으로 뺄 때까지 숨김)
export interface Section { label: string; icon: IconName; tabs: { id: string; label: string }[]; hidden?: boolean }

export type PlanningPart = 'planning' | 'dev'

/** 각 페이지가 화면을 그릴 때 받는 값 */
export interface View {
  state: State
  dispatch: Dispatch<Action>
  connected: boolean
  example: boolean
  section: string
  /** 메뉴 바로 다음 칸 (가로탭이 없는 메뉴에서는 필터·항목) */
  sub?: string
  tab?: string
  /** 가로탭 다음 칸들 (디코딩 전) */
  rest: string[]
  item?: string
  item2?: string
  /** 다른 부서에서 새로 들어온 요청·작업 연동 수, 소통 메시지 수 */
  newWork: number
  newTalk: number
}

export interface ShellProps {
  part: PlanningPart
  title: string
  desc: string
  home: string
  sections: Record<string, Section>
  /** 메뉴 옆 숫자 */
  badge: (v: View) => Record<string, number>
  /** 가로탭 옆 숫자 */
  tabCount?: (v: View, section: string, tab: string) => number
  /** 제목 줄 오른쪽에 더 붙일 것 (개발: AI와 대화·개발 도구) */
  tools?: (v: View) => ReactNode
  children: (v: View) => ReactNode
}

// /dev/work/prs/PR-28 → work/prs/PR-28. 예전 해시 주소(/dev#/work/prs)로 들어오면 경로로 옮긴다
function useRoute(base: string, home: string): string {
  const { pathname, hash } = useLocation()
  const navigate = useNavigate()
  setPlanningNav(base, navigate)
  useEffect(() => {
    if (hash.startsWith('#/')) navigate(`${base}/${hash.slice(2)}`, { replace: true })
  }, [hash]) // eslint-disable-line react-hooks/exhaustive-deps
  return pathname.slice(base.length).replace(/^\/+|\/+$/g, '') || home
}

// 화면 안 <a href="/dev/...">는 새로 불러오지 않고 react-router로 옮긴다 (새 탭·수정 키 클릭은 브라우저에 맡긴다)
function useInnerLinks() {
  const navigate = useNavigate()
  return (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    const a = (e.target as HTMLElement).closest('a')
    const href = a?.getAttribute('href')
    if (!a || !href || a.target || !href.startsWith('/') || href.startsWith('//')) return
    e.preventDefault()
    navigate(href)
  }
}

export function PlanningShell(props: ShellProps) {
  const onLinks = useInnerLinks()
  return (
    <div className="planning-scope" data-theme="light" onClick={onLinks}>
      <ToastProvider>
        <Frame key={props.part} {...props} />
      </ToastProvider>
    </div>
  )
}

const EXAMPLE_KEY = 'axcellent-planning-example'
// 예시의 AI 승인 감사는 "오늘" 건만 보여서, 예시 기록 날짜를 오늘로 옮긴다
const todayDemo = (s: State): State => ({ ...s, audit: s.audit.map((a) => ({ ...a, t: `${TODAY}${a.t.slice(5)}` })) })

function Frame({ part, title, desc, home, sections, badge, tabCount, tools, children }: ShellProps) {
  const isSection = (x: string) => x in sections
  const route = useRoute(`/${part}`, home)
  const [live, liveDispatch, connected] = useSharedStore(apiBase(part))
  // 예시 모드: 서버 대신 화면 안의 프로토타입 예시 데이터(planningData.ts)로 흐름을 보여 준다. 서버 데이터는 건드리지 않는다
  const [example, setExample] = useState(() => { try { return localStorage.getItem(EXAMPLE_KEY) === '1' } catch { return false } })
  const [demo, demoDispatch] = useReducer(reducer, initialState, todayDemo)
  const state = example ? demo : live
  const dispatch = example ? demoDispatch : liveDispatch
  const pickExample = (on: boolean) => {
    setExample(on)
    try { localStorage.setItem(EXAMPLE_KEY, on ? '1' : '0') } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ }
  }
  const toast = useToast()
  useEffect(() => {
    const on = (e: Event) => toast.show((e as CustomEvent<string>).detail)
    window.addEventListener(ACTION_ERROR_EVENT, on)
    return () => window.removeEventListener(ACTION_ERROR_EVENT, on)
  }, [toast])
  const [section, sub, ...rest] = route.split('/')
  const known = isSection(section)
  const tabs: { id: string; label: string }[] = known ? sections[section].tabs : []
  const tab = tabs.find((t) => t.id === sub)?.id ?? tabs[0]?.id
  // 가로탭이 있는 메뉴는 <메뉴>/<탭>/<항목>/<하위>, 없는 메뉴는 <메뉴>/<항목>/<하위>
  const [item, item2] = (tabs.length ? rest : [sub, ...rest]).map((x) => (x ? decodeURIComponent(x) : undefined))
  // 없는 화면 주소(예: 예전 '참여한 사건')는 첫 화면으로 돌린다
  useEffect(() => {
    if (!known) go(home, true)
  }, [known, section]) // eslint-disable-line react-hooks/exhaustive-deps

  // 다른 부서에서 들어온 것: 요청·작업 화면을 볼 때까지 "새로"로 센다
  const inbound = state.activity.filter((x) => x.seq && x.to === 'planning')
  const [seenSeq, setSeenSeq] = useState(0)
  const [talkSeen, setTalkSeen] = useState(0)
  const highlightFrom = useRef(0)
  useEffect(() => {
    if (section === 'requests' || section === 'work') {
      highlightFrom.current = seenSeq
      setSeenSeq(state.seq)
    }
    if (section === 'talk') setTalkSeen(state.seq)
  }, [section, state.seq]) // eslint-disable-line react-hooks/exhaustive-deps
  const newWork = inbound.filter((x) => x.point !== 'message' && (x.seq ?? 0) > seenSeq).length
  const newTalk = inbound.filter((x) => x.point === 'message' && (x.seq ?? 0) > talkSeen).length
  const recentIn = inbound.filter((x) => x.point !== 'message').slice(-3).reverse()

  const view: View = { state, dispatch, connected, example, section, sub, tab, rest, item, item2, newWork, newTalk }
  const counts = badge(view)
  const menu = Object.keys(sections).filter((id) => !sections[id].hidden).map((id) => ({
    href: toPath(`${id}${sections[id].tabs[0] ? `/${sections[id].tabs[0].id}` : ''}`),
    label: sections[id].label,
    on: section === id,
    badge: counts[id] || 0,
  }))

  return (
    <div className="main">
      <header className="pl-head">
        <div>
          <h1 className="pl-title">{title}</h1>
          <p className="pl-desc">{desc}</p>
        </div>
        <div className="pl-tools">
          <div className="seg-toggle" role="group" aria-label="데이터 보기">
            <button type="button" aria-pressed={!example} onClick={() => pickExample(false)}>실제 데이터</button>
            <button type="button" aria-pressed={example} onClick={() => pickExample(true)}>예시 데이터</button>
          </div>
          {example
            ? <Chip tone="amber" title="화면 안 예시 데이터. 서버에 반영되지 않아요">예시 모드</Chip>
            : <Chip tone={connected ? 'green' : 'amber'} title={`${title} 서버 ${apiBase(part)}`}>{`${title} ${connected ? '켜짐' : '꺼짐'}`}</Chip>}
          {tools?.(view)}
          <ToastOutlet />
        </div>
      </header>
      <div className="pl-bar">
        <nav className="pl-menu" aria-label={`${title} 메뉴`}>
          {menu.map((m) => (
            <a key={m.href} href={m.href} className={m.on ? 'on' : ''} aria-current={m.on ? 'page' : undefined}>
              {m.label}
              {m.badge > 0 && <span className="nav-badge">{m.badge}</span>}
            </a>
          ))}
        </nav>
      </div>

      {known ? (
        <main className="page page-wide">
          {tabs.length > 1 && (
            <div className="subtabs" role="tablist" aria-label={`${sections[section].label} 메뉴`}>
              {tabs.map((t) => {
                const n = tabCount?.(view, section, t.id) ?? 0
                return (
                  <a key={t.id} href={toPath(`${section}/${t.id}`)} className={t.id === tab ? 'on' : ''} role="tab" aria-selected={t.id === tab}>
                    {t.label}
                    {n > 0 && <span className="subtab-count">{n}</span>}
                  </a>
                )
              })}
            </div>
          )}
          {(section === 'requests' || section === 'work') && recentIn.length > 0 && (
            <div className="live-strip" aria-live="polite">
              <span className="live-strip-h">새로 받은 연동</span>
              {recentIn.map((x, i) => (
                <a key={i} href={convoHref(x.ctx)} className={`live-item${(x.seq ?? 0) > highlightFrom.current ? ' fresh' : ''}`}>
                  <Dep d={x.from} /> <Point>{x.point}</Point> <span className="muted small">{x.text}</span>
                </a>
              ))}
            </div>
          )}
          {children(view)}
        </main>
      ) : null}
    </div>
  )
}
