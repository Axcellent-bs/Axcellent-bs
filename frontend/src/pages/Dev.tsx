// 개발·배포 화면(/dev). 확정된 기획서를 작업·PR로 만들고, 교차검증·배포·사후관리까지 맡는다.
// 메뉴(사이드바 하위 탭 demo/services.ts) = 작업 현황(첫 화면)·작업(작업·PR·AI 교차검증·운영·배포·사후관리)·지표. 에이전트 대화는 메뉴에서 빼고 주소로만 연다. 틀은 기획 화면과 같이 쓴다(components/planning-common/Shell.tsx).
import { useState } from 'react'
import { Link, useRouter } from '@/lib/nav'
import { IconLink } from '@/components/icons'
import { PlanningShell, type Section, type View } from '@/components/planning-common/Shell'
import { prSlug, toPath } from '@/components/planning-common/links'
import { repoPending } from '@/components/planning-common/repo'
import { AgentTalk, Talk } from '@/components/planning-common/talk'
import { ChatPanel, useDevChat } from '@/components/dev/chat'
import { Activity, Audit, Autonomy } from '@/components/dev/metrics'
import { Aftercare, Ops } from '@/components/dev/ops'
import { PrsTab } from '@/components/dev/repows'
import { TaskBoard } from '@/components/dev/board'
import { Reviews } from '@/components/dev/work'
import '@/components/dev/hier.css'

const SECTIONS: Record<string, Section> = {
  tasks: { label: '작업 현황', icon: 'menu', tabs: [], head: { title: '작업 현황', desc: '개발 작업이 지금 어느 단계에 있는지 봐요.' } }, // 사람 확인·진행 중·머지됨·중단은 화면 안 필터 (tasks/<필터>)
  work: { label: '작업', icon: 'plan', head: { title: '작업', desc: '작업·PR, 교차검증, 배포, 장애 대응을 다뤄요.' }, tabs: [
    { id: 'prs', label: '작업·PR' },
    { id: 'reviews', label: 'AI 교차검증' },
    { id: 'ops', label: '배포' },
    { id: 'aftercare', label: '장애 대응' },
  ] },
  agents: { label: '에이전트 대화', icon: 'send', tabs: [], hidden: true },  // 메뉴에서 뺌 (주소로만 연다)
  talk: { label: '소통', icon: 'send', tabs: [], hidden: true },
  metrics: { label: '지표', icon: 'dashboard', head: { title: '지표', desc: '자동 머지 기준과 AI가 혼자 처리한 일을 봐요.' }, tabs: [
    { id: 'autonomy', label: '자동 머지 기준' },
    { id: 'audit', label: 'AI 단독 처리' },
    { id: 'activity', label: '에이전트 활동' },
  ] },
}

// 제목 줄 오른쪽: 레포 승인 대기 · AI 작업 중 · AI와 대화 · 연결(본인 Claude·레포·개발 도구)
function DevTools({ v }: { v: View }) {
  const { state, connected, example } = v
  const router = useRouter()
  const [chatOpen, setChatOpen] = useState(false)
  const chat = useDevChat(state, connected)
  const repoWait = repoPending(state)
  const firstRepoWait = state.prs.find((p) => p.target?.status === 'proposed')
  return (
    <>
      {repoWait > 0 && firstRepoWait && (
        <a href={toPath(`work/prs/${prSlug(firstRepoWait.id)}`)} className="chip amber" title="올릴 레포 승인이 필요한 PR">레포 승인 대기 {repoWait}</a>
      )}
      {chat.working.length > 0 && (
        <button type="button" className="chip amber" onClick={() => { chat.select(chat.working[0]); setChatOpen(true) }}>AI 작업 중 {chat.working.length}</button>
      )}
      <button type="button" className="btn-primary px-3 py-1" disabled={!connected || example} title={example ? '예시 모드에서는 쓸 수 없어요' : connected ? '개발 에이전트' : '개발·배포 서버 꺼짐'} onClick={() => setChatOpen(true)}>AI와 대화</button>
      <Link href="/connections" className="btn-quiet px-2 py-1" title="연결 (형상관리·AI)" aria-label="연결 (형상관리·AI)">
        <IconLink size={20} />
      </Link>
      {chatOpen && (
        <ChatPanel s={state} chat={chat} connected={connected} onClose={() => setChatOpen(false)} onTools={() => { setChatOpen(false); router.push('/connections') }} />
      )}
    </>
  )
}

export default function DevPage() {
  return (
    <PlanningShell
      part="dev"
      title="개발·배포"
      desc="확정된 기획서대로 코드를 고치고, 검토·배포·장애 대응까지 이어 갑니다."
      home="tasks"
      sections={SECTIONS}
      tabCount={(v, section, tab) => (section === 'work' && tab === 'reviews' ? v.state.reviews.filter((x) => !x.result).length : 0)}
      tools={(v) => <DevTools v={v} />}
    >
      {({ state: s, dispatch: d, section, sub, tab, rest, item, example }) => (
        <>
          {section === 'tasks' && <TaskBoard s={s} filter={sub} />}
          {section === 'work' && tab === 'prs' && <PrsTab s={s} d={d} example={example} parts={rest.map((x) => decodeURIComponent(x))} />}
          {section === 'work' && tab === 'reviews' && <Reviews s={s} d={d} item={item} />}
          {section === 'work' && tab === 'ops' && <Ops s={s} item={item} />}
          {section === 'work' && tab === 'aftercare' && <Aftercare s={s} item={item} />}
          {section === 'talk' && <Talk s={s} d={d} item={item} />}
          {section === 'agents' && <AgentTalk s={s} item={item} />}
          {section === 'metrics' && tab === 'autonomy' && <Autonomy s={s} d={d} item={item} />}
          {section === 'metrics' && tab === 'audit' && <Audit s={s} d={d} />}
          {section === 'metrics' && tab === 'activity' && <Activity s={s} item={item} />}
        </>
      )}
    </PlanningShell>
  )
}
