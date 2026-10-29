// 기획팀(/planning)·개발팀(/dev) 화면. AXcellent 안에서 돈다.
// 메뉴 = 프로젝트·요청·작업·정리·소통·규정·자료·지표, 각 메뉴의 하위 목록은 그 아래 가로탭. 화면 안 이동은 해시(#/<메뉴>/<가로탭>)로 한다.
// 공통 화면 컴포넌트(@dejafix/common-frontend)의 스타일은 .planning-scope 안에만 적용된다 (postcss.config.js).
import '@dejafix/common-frontend/styles.css'
import './planning.css'
import { useEffect, useRef, useState } from 'react'
import { Chip, ToastOutlet, ToastProvider, useToast, type NavItem } from '@dejafix/common-frontend'
import { ACTION_ERROR_EVENT, useSharedStore } from './store'
import { apiBase } from './api'
import { DevToolsDialog } from '@/demo/DevTools'
import { IconGear } from '@/components/icons'
import { Dep, Point } from './parts'
import { Activity, Aftercare, Audit, Autonomy, Links, Metrics, Ops, Reviews, Specs } from './pages'
import { PrsTab } from './repows'
import { Requests, incomingCount } from './requests'
import { Docs } from './docs'
import { AgentTalk, Talk } from './talk'
import { Projects } from './projects'
import { convoHref } from './links'
import { Knowledge } from './knowledge'
import { repoPending } from './repo'
import { ChatPanel, useDevChat } from './chat'
import { prSlug } from './links'

type IconName = NonNullable<NavItem['icon']>
// hidden: 메뉴에는 안 보이지만 주소로는 열린다 (소통은 부서 공용 탭으로 뺄 때까지 숨김)
interface Section { label: string; icon: IconName; tabs: { id: string; label: string }[]; hidden?: boolean }

// 데모에서 기획(요청을 기획서로)과 개발(작업·검증·배포·사후관리)을 두 부서로 나눴다. 데이터는 같은 기획개발 서비스에서 받는다.
export type PlanningPart = 'planning' | 'dev'
const PARTS: Record<PlanningPart, { title: string; desc: string; home: string; sections: Record<string, Section> }> = {
  planning: {
    title: '요청·기획서',
    desc: '다른 부서의 요청을 받아 기획서로 정리하고, 결과를 요청한 부서에 알립니다.',
    home: 'requests',
    sections: {
      projects: { label: '프로젝트', icon: 'menu', tabs: [] },
      requests: { label: '요청', icon: 'bell', tabs: [] }, // 들어온·진행 중·해결·후속은 화면 안 필터 (#/requests/<필터>)
      work: { label: '기획서', icon: 'plan', tabs: [{ id: 'specs', label: '기획서' }] },
      docs: { label: '정리', icon: 'report', tabs: [] },
      agents: { label: '에이전트 대화', icon: 'send', tabs: [] },
      talk: { label: '소통', icon: 'send', tabs: [], hidden: true },
      knowledge: { label: '규정·자료', icon: 'admin', tabs: [] },
      metrics: { label: '지표', icon: 'dashboard', tabs: [
        { id: 'overview', label: '지표' },
        { id: 'links', label: '부서 연동' },
      ] },
    },
  },
  dev: {
    title: '개발·배포',
    desc: '확정된 기획서를 작업·PR로 만들고, 교차검증·배포·사후관리까지 맡습니다.',
    home: 'work/prs',
    sections: {
      work: { label: '작업', icon: 'plan', tabs: [
        { id: 'prs', label: '작업·PR' },
        { id: 'reviews', label: 'AI 교차검증' },
        { id: 'ops', label: '운영·배포' },
        { id: 'aftercare', label: '사후관리' },
      ] },
      agents: { label: '에이전트 대화', icon: 'send', tabs: [] },
      talk: { label: '소통', icon: 'send', tabs: [], hidden: true },
      metrics: { label: '지표', icon: 'dashboard', tabs: [
        { id: 'autonomy', label: '자동 처리 범위' },
        { id: 'audit', label: 'AI 승인 감사' },
        { id: 'activity', label: '에이전트 활동' },
      ] },
    },
  },
}

function useRoute(home: string): string {
  const read = () => location.hash.replace(/^#\/?/, '') || home
  const [r, setR] = useState(read)
  useEffect(() => {
    const on = () => setR(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return r
}

export default function PlanningPage({ part = 'planning' }: { part?: PlanningPart }) {
  return (
    <div className="planning-scope" data-theme="light">
      <ToastProvider>
        <Planning key={part} part={part} />
      </ToastProvider>
    </div>
  )
}

function Planning({ part }: { part: PlanningPart }) {
  const P = PARTS[part]
  const SECTIONS = P.sections
  const isSection = (x: string) => x in SECTIONS
  const route = useRoute(P.home)
  const [state, dispatch, connected] = useSharedStore(apiBase(part))
  const [tools, setTools] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const toast = useToast()
  useEffect(() => {
    const on = (e: Event) => toast.show((e as CustomEvent<string>).detail)
    window.addEventListener(ACTION_ERROR_EVENT, on)
    return () => window.removeEventListener(ACTION_ERROR_EVENT, on)
  }, [toast])
  const [section, sub, ...rest] = route.split('/')
  const planning = isSection(section)
  const tabs: { id: string; label: string }[] = planning ? SECTIONS[section].tabs : []
  const tab = tabs.find((t) => t.id === sub)?.id ?? tabs[0]?.id
  // 가로탭이 있는 메뉴는 #/<메뉴>/<탭>/<항목>/<하위>, 없는 메뉴는 #/<메뉴>/<항목>/<하위>
  const [item, item2] = (tabs.length ? rest : [sub, ...rest]).map((x) => (x ? decodeURIComponent(x) : undefined))
  // 없는 화면 주소(예: 예전 '참여한 사건')는 들어온 요청으로 돌린다
  useEffect(() => {
    if (!planning) location.replace(`#/${P.home}`)
  }, [planning, section]) // eslint-disable-line react-hooks/exhaustive-deps

  // 다른 부서에서 들어온 것: 기획개발 화면(요청·작업)을 볼 때까지 "새로"로 센다
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
  const incoming = incomingCount(state)
  const recentIn = inbound.filter((x) => x.point !== 'message').slice(-3).reverse()
  const repoWait = part === 'dev' ? repoPending(state) : 0
  const firstRepoWait = state.prs.find((p) => p.target?.status === 'proposed')
  const chat = useDevChat(state, part === 'dev' && connected)

  const badge: Record<string, number> = part === 'planning' ? { requests: newWork || incoming, talk: newTalk } : { talk: newTalk }
  const menu = [
    ...Object.keys(SECTIONS).filter((id) => !SECTIONS[id].hidden).map((id) => ({
      href: `#/${id}${SECTIONS[id].tabs[0] ? `/${SECTIONS[id].tabs[0].id}` : ''}`,
      label: SECTIONS[id].label,
      on: section === id,
      badge: badge[id] || 0,
    })),
  ]

  return (
    <div className="main">
      <header className="pl-head">
        <div>
          <h1 className="pl-title">{P.title}</h1>
          <p className="pl-desc">{P.desc}</p>
        </div>
        <div className="pl-tools">
          <Chip tone={connected ? 'green' : 'amber'} title={`${P.title} 서버 ${apiBase(part)}`}>
            {`${P.title} ${connected ? '켜짐' : '꺼짐'}`}
          </Chip>
          {repoWait > 0 && firstRepoWait && (
            <a href={`#/work/prs/${prSlug(firstRepoWait.id)}`} className="chip amber" title="대상 레포 승인 대기 PR">레포 승인 대기 {repoWait}</a>
          )}
          {part === 'dev' && chat.working.length > 0 && (
            <button type="button" className="chip amber" onClick={() => { chat.select(chat.working[0]); setChatOpen(true) }}>AI 작업 중 {chat.working.length}</button>
          )}
          {part === 'dev' && (
            <button type="button" className="btn-primary px-3 py-1" disabled={!connected} title={connected ? '개발 에이전트' : '개발·배포 서버 꺼짐'} onClick={() => setChatOpen(true)}>AI와 대화</button>
          )}
          {part === 'dev' && (
            <button type="button" className="btn-quiet px-2 py-1" title="개발 도구 연결" aria-label="개발 도구 연결" onClick={() => setTools(true)}>
              <IconGear size={20} />
            </button>
          )}
          <ToastOutlet />
        </div>
      </header>
      {tools && <DevToolsDialog onClose={() => setTools(false)} />}
      {part === 'dev' && chatOpen && (
        <ChatPanel s={state} chat={chat} connected={connected} onClose={() => setChatOpen(false)} onTools={() => { setChatOpen(false); setTools(true) }} />
      )}
      <div className="pl-bar">
        <nav className="pl-menu" aria-label={`${P.title} 메뉴`}>
          {menu.map((m) => (
            <a key={m.href} href={m.href} className={m.on ? 'on' : ''} aria-current={m.on ? 'page' : undefined}>
              {m.label}
              {m.badge > 0 && <span className="nav-badge">{m.badge}</span>}
            </a>
          ))}
        </nav>
      </div>

      {planning ? (
        <main className="page page-wide">
          {tabs.length > 1 && (
            <div className="subtabs" role="tablist" aria-label={`${SECTIONS[section].label} 메뉴`}>
              {tabs.map((t) => {
                const n = section === 'work' && t.id === 'reviews' ? state.reviews.filter((x) => !x.result).length : 0
                return (
                  <a key={t.id} href={`#/${section}/${t.id}`} className={t.id === tab ? 'on' : ''} role="tab" aria-selected={t.id === tab}>
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
          {section === 'requests' && <Requests s={state} d={dispatch} filter={sub} item={rest[0] ? decodeURIComponent(rest[0]) : undefined} />}
          {section === 'work' && tab === 'specs' && <Specs s={state} d={dispatch} item={item} item2={item2} />}
          {section === 'work' && tab === 'prs' && <PrsTab s={state} d={dispatch} parts={rest.map((x) => decodeURIComponent(x))} />}
          {section === 'work' && tab === 'reviews' && <Reviews s={state} d={dispatch} item={item} />}
          {section === 'work' && tab === 'ops' && <Ops s={state} item={item} />}
          {section === 'work' && tab === 'aftercare' && <Aftercare s={state} item={item} />}
          {section === 'docs' && <Docs s={state} d={dispatch} item={item} />}
          {section === 'talk' && <Talk s={state} d={dispatch} item={item} />}
          {section === 'agents' && <AgentTalk s={state} item={item} />}
          {section === 'knowledge' && <Knowledge />}
          {section === 'projects' && <Projects s={state} d={dispatch} sel={item} />}
          {section === 'metrics' && tab === 'overview' && <Metrics s={state} />}
          {section === 'metrics' && tab === 'autonomy' && <Autonomy s={state} d={dispatch} item={item} />}
          {section === 'metrics' && tab === 'audit' && <Audit s={state} d={dispatch} />}
          {section === 'metrics' && tab === 'activity' && <Activity s={state} item={item} />}
          {section === 'metrics' && tab === 'links' && <Links s={state} d={dispatch} />}
        </main>
      ) : null}

    </div>
  )
}
