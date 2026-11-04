// 기획 화면(/planning). 다른 부서의 요청을 받아 기획서로 정리하고, 결과를 요청한 부서에 알린다.
// 메뉴 = 요청·기획서·에이전트 대화 (기획 AI 진행 상황만 본다, 사이드바 하위 탭 demo/services.ts). 프로젝트·정리·규정·자료·지표는 메뉴에서 숨기고 주소로만 연다.
// 틀은 개발 화면과 같이 쓴다(components/planning-common/Shell.tsx).
import { PlanningShell, type Section } from '@/components/planning-common/Shell'
import { AgentTalk, Talk } from '@/components/planning-common/talk'
import { Docs } from '@/components/planning/docs'
import { Knowledge } from '@/components/planning/knowledge'
import { Links, Metrics } from '@/components/planning/metrics'
import { Projects } from '@/components/planning/projects'
import { Requests } from '@/components/planning/requests'
import { Specs } from '@/components/planning/specs'

const SECTIONS: Record<string, Section> = {
  requests: { label: '요청', icon: 'bell', tabs: [] }, // 들어온·진행 중·해결·후속은 화면 안 필터 (requests/<필터>)
  work: { label: '문서·기획서', icon: 'plan', tabs: [{ id: 'specs', label: '문서·기획서' }] },
  agents: { label: '에이전트 대화', icon: 'send', tabs: [] },
  talk: { label: '소통', icon: 'send', tabs: [], hidden: true },
  projects: { label: '프로젝트', icon: 'menu', tabs: [], hidden: true }, // 요청 줄 제목을 누르면 요청 1건을 깊게
  docs: { label: '정리', icon: 'report', tabs: [], hidden: true }, // 메뉴에서 뺌 (요청 정리는 기획서가 맡는다). AI 대화가 만든 DOC 링크만 연다
  knowledge: { label: '규정·자료', icon: 'admin', tabs: [], hidden: true },
  metrics: { label: '지표', icon: 'dashboard', hidden: true, tabs: [
    { id: 'overview', label: '지표' },
    { id: 'links', label: '부서 연동' },
  ] },
}

export default function PlanningPage() {
  return (
    <PlanningShell
      part="planning"
      title="요청·기획서"
      desc="다른 부서의 요청을 받아 기획서로 정리하고, 결과를 요청한 부서에 알립니다."
      home="requests"
      sections={SECTIONS}
    >
      {({ state: s, dispatch: d, section, sub, tab, rest, item, item2 }) => (
        <>
          {section === 'requests' && <Requests s={s} d={d} filter={sub} item={rest[0] ? decodeURIComponent(rest[0]) : undefined} />}
          {section === 'work' && tab === 'specs' && <Specs s={s} d={d} item={item} item2={item2} />}
          {section === 'docs' && <Docs s={s} d={d} item={item} />}
          {section === 'talk' && <Talk s={s} d={d} item={item} />}
          {section === 'agents' && <AgentTalk s={s} item={item} />}
          {section === 'knowledge' && <Knowledge />}
          {section === 'projects' && <Projects s={s} d={d} sel={item} />}
          {section === 'metrics' && tab === 'overview' && <Metrics s={s} />}
          {section === 'metrics' && tab === 'links' && <Links s={s} d={d} />}
        </>
      )}
    </PlanningShell>
  )
}
