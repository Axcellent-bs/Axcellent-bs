// 개발·배포 화면(/dev). 확정된 기획서를 작업·PR로 만들고, 교차검증·배포·사후관리까지 맡는다.
// 메뉴 = 작업(작업·PR·AI 교차검증·운영·배포·사후관리)·에이전트 대화·지표. 틀은 기획 화면과 같이 쓴다(components/planning-common/Shell.tsx).
import { useState } from 'react'
import { DevToolsDialog } from '@/demo/DevTools'
import { IconGear } from '@/components/icons'
import { PlanningShell, type Section, type View } from '@/components/planning-common/Shell'
import { prSlug, toPath } from '@/components/planning-common/links'
import { repoPending } from '@/components/planning-common/repo'
import { AgentTalk, Talk } from '@/components/planning-common/talk'
import { ChatPanel, useDevChat } from '@/components/dev/chat'
import { Activity, Audit, Autonomy } from '@/components/dev/metrics'
import { Aftercare, Ops } from '@/components/dev/ops'
import { PrsTab } from '@/components/dev/repows'
import { Reviews } from '@/components/dev/work'

const SECTIONS: Record<string, Section> = {
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
}

// 제목 줄 오른쪽: 레포 승인 대기 · AI 작업 중 · AI와 대화 · 개발 도구 연결
function DevTools({ v }: { v: View }) {
  const { state, connected, example } = v
  const [tools, setTools] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const chat = useDevChat(state, connected)
  const repoWait = repoPending(state)
  const firstRepoWait = state.prs.find((p) => p.target?.status === 'proposed')
  return (
    <>
      {repoWait > 0 && firstRepoWait && (
        <a href={toPath(`work/prs/${prSlug(firstRepoWait.id)}`)} className="chip amber" title="대상 레포 승인 대기 PR">레포 승인 대기 {repoWait}</a>
      )}
      {chat.working.length > 0 && (
        <button type="button" className="chip amber" onClick={() => { chat.select(chat.working[0]); setChatOpen(true) }}>AI 작업 중 {chat.working.length}</button>
      )}
      <button type="button" className="btn-primary px-3 py-1" disabled={!connected || example} title={example ? '예시 모드에서는 쓸 수 없어요' : connected ? '개발 에이전트' : '개발·배포 서버 꺼짐'} onClick={() => setChatOpen(true)}>AI와 대화</button>
      <button type="button" className="btn-quiet px-2 py-1" title="개발 도구 연결" aria-label="개발 도구 연결" onClick={() => setTools(true)}>
        <IconGear size={20} />
      </button>
      {tools && <DevToolsDialog onClose={() => setTools(false)} />}
      {chatOpen && (
        <ChatPanel s={state} chat={chat} connected={connected} onClose={() => setChatOpen(false)} onTools={() => { setChatOpen(false); setTools(true) }} />
      )}
    </>
  )
}

export default function DevPage() {
  return (
    <PlanningShell
      part="dev"
      title="개발·배포"
      desc="확정된 기획서를 작업·PR로 만들고, 교차검증·배포·사후관리까지 맡습니다."
      home="work/prs"
      sections={SECTIONS}
      badge={(v) => ({ talk: v.newTalk })}
      tabCount={(v, section, tab) => (section === 'work' && tab === 'reviews' ? v.state.reviews.filter((x) => !x.result).length : 0)}
      tools={(v) => <DevTools v={v} />}
    >
      {({ state: s, dispatch: d, section, tab, rest, item }) => (
        <>
          {section === 'work' && tab === 'prs' && <PrsTab s={s} d={d} parts={rest.map((x) => decodeURIComponent(x))} />}
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
