// 서비스 카탈로그. 부서를 코드에 박지 않으려고 부서 묶음을 없애고, 부서 안에 있던 기능을 서비스 하나하나로 꺼냈다.
// 회사는 Adobe Creative Cloud처럼 필요한 서비스만 골라 쓰고(/system/services), 켠 서비스는 시스템 관리자 메뉴에 전부 보인다(session.tsx).
// 화면은 예전 캣북스 대시보드의 AI 화면을 옮겨 온 것이다. paths가 그 서비스에 속한 화면 주소, agent가 맡은 AI다.
import { useSyncExternalStore, type ComponentType } from 'react'
import {
  IconBolt, IconBook, IconBox, IconCart, IconChart, IconClock, IconCode, IconMegaphone, IconOffice, IconPlan, IconReceipt, IconShield, IconStar, IconUser, IconUsers,
} from '@/components/icons'

export type SubItem = { href: string; label: string; soon?: boolean }
/** 제품군. 카탈로그(메인 페이지)에서 묶어 보여 줄 때만 쓴다. 부서가 아니다 */
export type Family = 'infra' | 'customer' | 'growth' | 'backoffice' | 'product' | 'together'
export const FAMILIES: { id: Family; name: string; desc: string }[] = [
  { id: 'infra', name: '인프라', desc: '서버와 PC를 지키는 DejaFix' },
  { id: 'customer', name: '고객 경험', desc: '문의·클레임·리뷰를 AI가 먼저 처리' },
  { id: 'growth', name: '마케팅·데이터', desc: '공지·쿠폰과 숫자로 보는 성과' },
  { id: 'backoffice', name: '경영지원', desc: '돈·사람·결정 기록' },
  { id: 'product', name: '제품 개발', desc: '요청에서 배포까지' },
  { id: 'together', name: '조율', desc: '여러 서비스에 걸친 일을 한 번에' },
]

/**
 * AXcellent가 관리하는 AI 8개. 예전 캣북스 대시보드(ax-frontend dashboard-web)에 있던 AI를 모두 이리로 옮겼다.
 * id는 ax-backend .github/team.yml 부서 id, backend는 화면이 부르는 서버(src/lib/env.ts).
 * 왼쪽 메뉴는 이 순서대로 AI가 상위 탭이고, 그 AI가 맡은 서비스가 하위 탭이다 (AdminShell).
 * 안전 관문(safety)·회사 기억(memory)은 AI 화면이 아니라 뒤에서 도는 서버라 넣지 않는다. 인프라는 DejaFix가 맡는다.
 */
export type AgentId = 'orchestrator' | 'support' | 'finance' | 'data' | 'hr' | 'marketing' | 'planning' | 'dev'
export const AGENTS: { id: AgentId; name: string; Icon: ComponentType<{ size?: number }>; backend: string }[] = [
  { id: 'orchestrator', Icon: IconBolt, name: '총괄', backend: 'orchestrator (8000) · shop-api 관리 중계 (8010)' },
  { id: 'support', Icon: IconUser, name: '고객지원 AI', backend: 'dashboard-api (8020)' },
  { id: 'finance', Icon: IconShield, name: '재무 AI', backend: 'finance (8105) · safety (8001)' },
  { id: 'data', Icon: IconChart, name: '데이터분석 AI', backend: 'data (8106)' },
  { id: 'hr', Icon: IconUsers, name: '인사 AI', backend: 'dashboard-api (8020)' },
  { id: 'marketing', Icon: IconMegaphone, name: '마케팅 AI', backend: '연결 전 (화면 안 시연 데이터)' },
  { id: 'planning', Icon: IconPlan, name: '기획 AI', backend: 'planning (8101) · dashboard-api (8020)' },
  { id: 'dev', Icon: IconCode, name: '개발 AI', backend: 'dev (8111)' },
]

export type Service = {
  id: string
  name: string
  family: Family
  /** 만든 쪽. DejaFix 서비스는 DejaFix에서 데이터를 받아 AXcellent가 그린다 */
  by: 'AXcellent' | 'DejaFix'
  /** 이 서비스를 맡은 AI (DejaFix 서비스는 없음) */
  agent?: AgentId
  desc: string
  Icon: ComponentType<{ size?: number }>
  logo?: string
  /** 메뉴를 누르면 여는 화면 */
  entry: string
  /** 이 서비스에 속한 화면 주소 앞부분. '/'는 그 주소 하나만 뜻한다 */
  paths: string[]
  /** 서비스 안 하위 메뉴 (사이드바 하위 항목, 현재 UI에서는 상단 둘째 줄) */
  children?: SubItem[]
}

const DEJAFIX_LOGO = `${import.meta.env.BASE_URL}dejafix-logo.png`

export const SERVICES: Service[] = [
  {
    id: 'incident', family: 'infra', name: '인프라 AI', by: 'DejaFix', Icon: IconBox, logo: DEJAFIX_LOGO, entry: '/infra', paths: ['/infra'],
    desc: '장애가 나면 원인 배포를 진단하고 롤백·복구 확인을 합니다. 지금 진행 중인 서버 장애와 요약은 DejaFix에서 받아 보여 줍니다.',
  },
  {
    id: 'hosts', family: 'infra', name: 'PC·서버 연결', by: 'DejaFix', Icon: IconBox, logo: DEJAFIX_LOGO, entry: '/hosts', paths: ['/hosts'],
    desc: '회사 PC와 서버를 사내 IP로 등록하고 연결 상태를 확인합니다.',
  },
  {
    id: 'inquiry', agent: 'support', family: 'customer', name: '고객 문의', by: 'AXcellent', Icon: IconUser, entry: '/inquiry', paths: ['/inquiry', '/console', '/support', '/voice'],
    desc: '문의를 분류하고 답변 초안을 쓰며, 상담원은 콘솔에서 확인해 보냅니다.',
    children: [
      { href: '/inquiry', label: '문의 현황' },
      { href: '/console', label: '상담 콘솔' },
      { href: '/support', label: 'AI 처리 내역' },
      { href: '/voice', label: '고객의 소리' },
    ],
  },
  {
    id: 'orders', agent: 'support', family: 'customer', name: '주문 관리', by: 'AXcellent', Icon: IconCart, entry: '/orders', paths: ['/orders'],
    desc: '판매 서버의 주문을 최신순으로 보고, 취소·부분 환불·전체 환불을 따로 걸러 봅니다. 쇼핑몰이 아닌 회사는 꺼 두면 됩니다.',
  },
  {
    id: 'claims', agent: 'support', family: 'customer', name: '클레임·보상', by: 'AXcellent', Icon: IconReceipt, entry: '/claims', paths: ['/claims', '/queue'],
    desc: '환불·교환 신청을 확인하고 보상을 규칙대로 처리합니다.',
    children: [
      { href: '/claims', label: '클레임' },
      { href: '/queue', label: '보상 큐' },
    ],
  },
  {
    id: 'reviews', agent: 'support', family: 'customer', name: '리뷰·지식', by: 'AXcellent', Icon: IconStar, entry: '/reviews', paths: ['/reviews', '/kb'],
    desc: '후기에 답하고, 답변에 쓰는 지식 문서를 관리합니다.',
    children: [
      { href: '/reviews', label: '후기 관리' },
      { href: '/kb', label: '지식베이스' },
    ],
  },
  {
    id: 'coupon', agent: 'marketing', family: 'growth', name: '공지·쿠폰', by: 'AXcellent', Icon: IconMegaphone, entry: '/marketing', paths: ['/marketing'],
    desc: '공지 초안을 쓰고 쿠폰 발급을 기록·점검합니다.',
  },
  {
    id: 'finance', agent: 'finance', family: 'backoffice', name: '재무 관리', by: 'AXcellent', Icon: IconShield, entry: '/finance', paths: ['/finance'],
    desc: '결산·대사·정산과 장애 보상 장부, 이벤트 후 감사를 맡습니다.',
    // 화면 안 전환(pages/Finance.tsx)을 사이드바 하위 탭으로 연다. ①~⑥ 업무 탭은 평상시 화면 안에 둔다
    children: [
      { href: '/finance', label: '장애 이벤트 재무' },
      { href: '/finance/daily', label: '평상시 재무 업무' },
    ],
  },
  {
    id: 'data', agent: 'data', family: 'growth', name: '데이터 분석', by: 'AXcellent', Icon: IconChart, entry: '/data', paths: ['/data'],
    desc: '사건 영향을 숫자로 세고 학습 지표를 봅니다.',
    // 화면 안 전환(pages/Data.tsx)을 사이드바 하위 탭으로 연다
    children: [
      { href: '/data', label: '장애 영향 분석' },
      { href: '/data/ops', label: '운영 지표' },
      { href: '/data/trend', label: '트렌드&콘텐츠' },
    ],
  },
  {
    id: 'decisions', agent: 'hr', family: 'backoffice', name: '결정 기록', by: 'AXcellent', Icon: IconBook, entry: '/ops', paths: ['/ops'],
    desc: '회사의 결정을 기록하고, 새 계획이 과거에 기각된 안과 겹치는지 봅니다.',
  },
  {
    id: 'agent-eval', agent: 'hr', family: 'backoffice', name: '에이전트 평가', by: 'AXcellent', Icon: IconClock, entry: '/agent-eval', paths: ['/agent-eval'],
    desc: '장애가 났을 때 각 에이전트가 맡은 일을 제때 끝냈는지 봅니다. 점수나 순위 없이 늦은 이유를 함께 보여 줍니다.',
  },
  {
    id: 'org', agent: 'hr', family: 'backoffice', name: '조직 변동', by: 'AXcellent', Icon: IconOffice, entry: '/org', paths: ['/org'],
    desc: '팀 이동·입사·퇴사를 기록하고 확정합니다. 확정한 내용으로 센 팀별 인원이 재무 AI의 팀 예산에 쓰입니다.',
  },
  {
    id: 'planning', agent: 'planning', family: 'product', name: '요청·기획서', by: 'AXcellent', Icon: IconPlan, entry: '/planning/home', paths: ['/planning'],
    desc: '들어온 요청을 정리해 기획서로 만들고, 결과를 요청한 곳에 알립니다.',
    // 화면 안 메뉴(pages/Planning.tsx SECTIONS)를 사이드바 하위 탭으로 연다
    children: [
      { href: '/planning/home', label: '홈' },
      { href: '/planning/requests', label: '요청' },
      { href: '/planning/proposals', label: '기획 제안' },
      { href: '/planning/work', label: '문서·기획서' },
    ],
  },
  {
    id: 'dev', agent: 'dev', family: 'product', name: '개발·배포', by: 'AXcellent', Icon: IconCode, entry: '/dev/tasks', paths: ['/dev'],
    desc: '확정된 기획서대로 코드를 고치고, 검토·배포·장애 대응까지 이어 갑니다.',
    // 화면 안 메뉴(pages/Dev.tsx SECTIONS)를 사이드바 하위 탭으로 연다
    children: [
      { href: '/dev/tasks', label: '작업 현황' },
      { href: '/dev/work', label: '작업' },
      { href: '/dev/metrics', label: '지표' },
    ],
  },
  {
    id: 'orchestrator', agent: 'orchestrator', family: 'together', name: '총괄·사건', by: 'AXcellent', Icon: IconBolt, entry: '/orchestrator', paths: ['/orchestrator'],
    desc: '여러 서비스에 걸친 사건을 묶어 조율하고 사람 승인을 받습니다.',
  },
]

/** 서비스를 맡은 AI 이름 (DejaFix 서비스는 없음) */
export const agentName = (s: Service) => AGENTS.find((g) => g.id === s.agent)?.name

/** 서비스를 고르지 않아도 모두 쓰는 화면. 할 일함은 꺼진 서비스로 가던 일이 사람에게 쌓이는 곳이라 어느 서비스를 샀든 보인다 */
export const SHARED_PATHS = ['/handoffs', '/agents/chat', '/agents/office', '/connections']

export const matchPath = (base: string, path: string) =>
  base === '/' ? path === '/' : path === base || path.startsWith(`${base}/`)

export const serviceOfPath = (path: string) => SERVICES.find((s) => s.paths.some((p) => matchPath(p, path)))

/** 하위 메뉴 중 지금 주소에 가장 길게 맞는 항목 */
export const activeChild = (path: string, items: SubItem[]) =>
  items.filter((c) => matchPath(c.href, path)).sort((a, b) => b.href.length - a.href.length)[0]?.href

// ---------------------------------------------------------------- 회사가 고른 서비스
// 데모라 브라우저에만 기억한다. 메뉴·접근 검사·카탈로그 화면이 같은 값을 보도록 작은 저장소로 둔다.
const KEY = 'axcellent-demo-services'
// 저장한 서비스 선택과 함께, 그때 있던 서비스 목록도 남긴다. 그래야 그 뒤에 새로 생긴 서비스(예: 에이전트 평가)를
// "끈 것"이 아니라 "새로 생긴 것"으로 알아보고 켜 둘 수 있다
const KNOWN_KEY = 'axcellent-demo-services-known'
// 위 목록을 남기기 전에 선택을 저장해 둔 브라우저를 위해, 그 뒤에 새로 생긴 서비스를 적어 둔다
const ADDED_BEFORE_KNOWN_LIST = ['agent-eval', 'orders']
const ALL = SERVICES.map((s) => s.id)
// 필수 서비스: 회사가 끌 수 없고 카탈로그에서 내릴 수도 없다. 다른 AI가 장애 기록(총괄 사건·고객지원 장애 확인·
// 재무 장애 비용·인사 대응 시간)을 쓰므로 장애 대응과 그 감시 대상인 PC·서버 연결은 늘 켜 둔다
export const REQUIRED_SERVICES = ['incident', 'hosts']
export const isRequired = (id: string) => REQUIRED_SERVICES.includes(id)
const withRequired = (ids: string[]) => [...REQUIRED_SERVICES.filter((id) => !ids.includes(id)), ...ids]
const save = (ids: string[]) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(ids))
    localStorage.setItem(KNOWN_KEY, JSON.stringify(ALL))
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 전부 켠 상태로 돌아간다 */
  }
}
const read = (): string[] => {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return ALL
    const ids = (JSON.parse(raw) as string[]).filter((id) => ALL.includes(id))
    const knownRaw = localStorage.getItem(KNOWN_KEY)
    const known = knownRaw ? (JSON.parse(knownRaw) as string[]) : null
    const fresh = (known ? ALL.filter((id) => !known.includes(id)) : ADDED_BEFORE_KNOWN_LIST).filter(
      (id) => ALL.includes(id) && !ids.includes(id),
    )
    const merged = withRequired([...ids, ...fresh])
    if (fresh.length || !known) save(merged) // 새 서비스를 켠 채로 남기고, 다음부터는 목록으로 알아본다
    return merged
  } catch {
    return ALL
  }
}
let enabled = read()
const listeners = new Set<() => void>()

export const getEnabled = () => enabled
export function setEnabled(ids: string[]) {
  enabled = withRequired(ids)
  save(enabled)
  listeners.forEach((l) => l())
}
export const toggleService = (id: string) => {
  if (isRequired(id)) return
  setEnabled(enabled.includes(id) ? enabled.filter((x) => x !== id) : [...enabled, id])
}

export function useEnabledServices() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    getEnabled,
  )
}

// ---------------------------------------------------------------- AXcellent가 공개한 서비스 (관제 센터 서비스 관리)
// 운영팀이 카탈로그에서 내린 서비스는 메인 카탈로그와 고객사 서비스 고르기에 안 보인다. 이미 쓰고 있는 고객사는 그대로 쓴다.
const HIDDEN_KEY = 'axcellent-demo-hidden-services'
let hidden: string[] = (() => {
  try {
    return (JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]') as string[]).filter((id) => !isRequired(id))
  } catch {
    return []
  }
})()
const hiddenListeners = new Set<() => void>()
export const getHidden = () => hidden
export function toggleHidden(id: string) {
  if (isRequired(id)) return
  hidden = hidden.includes(id) ? hidden.filter((x) => x !== id) : [...hidden, id]
  try {
    localStorage.setItem(HIDDEN_KEY, JSON.stringify(hidden))
  } catch {
    /* 새로고침하면 전부 공개 상태로 돌아간다 */
  }
  hiddenListeners.forEach((l) => l())
}
export function useHiddenServices() {
  return useSyncExternalStore(
    (l) => {
      hiddenListeners.add(l)
      return () => hiddenListeners.delete(l)
    },
    getHidden,
  )
}
/** 카탈로그에 공개 중인 서비스 */
export function usePublishedServices() {
  const h = useHiddenServices()
  return SERVICES.filter((s) => !h.includes(s.id))
}
