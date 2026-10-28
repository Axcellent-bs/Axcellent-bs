// 서비스 카탈로그. 부서를 코드에 박지 않으려고 부서 묶음을 없애고, 부서 안에 있던 기능을 서비스 하나하나로 꺼냈다.
// 회사는 Adobe Creative Cloud처럼 필요한 서비스만 골라 쓰고(/system/services), 켠 서비스는 시스템 관리자 메뉴에 전부 보인다(session.tsx).
// 화면은 예전 캣북스 대시보드의 AI 화면을 옮겨 온 것이다. paths가 그 서비스에 속한 화면 주소, agent가 맡은 AI다.
import { useSyncExternalStore, type ComponentType } from 'react'
import {
  IconBolt, IconBook, IconBox, IconChart, IconCode, IconMegaphone, IconPlan, IconReceipt, IconShield, IconStar, IconUser, IconUsers,
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
 * AXcellent가 관리하는 AI 7개. 예전 캣북스 대시보드(ax-frontend dashboard-web)에 있던 AI를 모두 이리로 옮겼다.
 * id는 ax-backend .github/team.yml 부서 id, backend는 화면이 부르는 서버(src/lib/env.ts).
 * 왼쪽 메뉴는 이 순서대로 AI가 상위 탭이고, 그 AI가 맡은 서비스가 하위 탭이다 (AdminShell).
 * 안전 관문(safety)·회사 기억(memory)은 AI 화면이 아니라 뒤에서 도는 서버라 넣지 않는다. 인프라는 DejaFix가 맡는다.
 */
export type AgentId = 'orchestrator' | 'support' | 'finance' | 'data' | 'hr' | 'marketing' | 'planning'
export const AGENTS: { id: AgentId; name: string; Icon: ComponentType<{ size?: number }>; backend: string }[] = [
  { id: 'orchestrator', Icon: IconBolt, name: '총괄', backend: 'orchestrator (8000) · shop-api 관리 중계 (8010)' },
  { id: 'support', Icon: IconUser, name: '고객지원 AI', backend: 'dashboard-api (8020)' },
  { id: 'finance', Icon: IconShield, name: '재무 AI', backend: 'finance (8105) · safety (8001)' },
  { id: 'data', Icon: IconChart, name: '데이터분석 AI', backend: 'data (8106)' },
  { id: 'hr', Icon: IconUsers, name: '인사 AI', backend: 'dashboard-api (8020)' },
  { id: 'marketing', Icon: IconMegaphone, name: '마케팅 AI', backend: '연결 전 (화면 안 시연 데이터)' },
  { id: 'planning', Icon: IconPlan, name: '기획개발 AI', backend: 'planning (8101) · dashboard-api (8020)' },
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
    id: 'incident', family: 'infra', name: '장애 대응', by: 'DejaFix', Icon: IconBox, logo: DEJAFIX_LOGO, entry: '/infra', paths: ['/infra'],
    desc: '지금 진행 중인 서버 장애와 가장 급한 장애·요약·시간대별 분포를 DejaFix에서 받아 보여 줍니다.',
  },
  {
    id: 'hosts', family: 'infra', name: 'PC·서버 연결', by: 'DejaFix', Icon: IconBox, logo: DEJAFIX_LOGO, entry: '/hosts', paths: ['/hosts'],
    desc: '회사 PC와 서버를 사내 IP로 등록하고 연결 상태를 확인합니다.',
  },
  {
    id: 'inquiry', agent: 'support', family: 'customer', name: '고객 문의', by: 'AXcellent', Icon: IconUser, entry: '/inquiry', paths: ['/inquiry', '/console', '/support'],
    desc: '문의를 분류하고 답변 초안을 쓰며, 상담원은 콘솔에서 확인해 보냅니다.',
    children: [
      { href: '/inquiry', label: '문의 현황' },
      { href: '/console', label: '상담 콘솔' },
      { href: '/support', label: 'AI 처리 내역' },
    ],
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
    desc: '결산·대사·정산과 장애 보상 장부, 사건 후 감사를 맡습니다.',
  },
  {
    id: 'data', agent: 'data', family: 'growth', name: '데이터 분석', by: 'AXcellent', Icon: IconChart, entry: '/data', paths: ['/data'],
    desc: '사건 영향을 숫자로 세고 학습 지표를 봅니다.',
  },
  {
    id: 'handover', agent: 'hr', family: 'backoffice', name: '인수인계', by: 'AXcellent', Icon: IconUsers, entry: '/hr', paths: ['/hr'],
    desc: '담당이 바뀔 때 넘길 일을 모으고 담당 명부를 관리합니다.',
  },
  {
    id: 'decisions', agent: 'hr', family: 'backoffice', name: '결정 기록', by: 'AXcellent', Icon: IconBook, entry: '/ops', paths: ['/ops'],
    desc: '회사의 결정을 기록하고, 새 계획이 과거에 기각된 안과 겹치는지 봅니다.',
  },
  {
    id: 'planning', agent: 'planning', family: 'product', name: '요청·기획서', by: 'AXcellent', Icon: IconPlan, entry: '/planning', paths: ['/planning'],
    desc: '들어온 요청을 정리해 기획서로 만들고, 결과를 요청한 곳에 알립니다.',
  },
  {
    id: 'dev', agent: 'planning', family: 'product', name: '개발·배포', by: 'AXcellent', Icon: IconCode, entry: '/dev', paths: ['/dev'],
    desc: '기획서를 작업·PR로 만들고 교차검증·배포·사후관리까지 맡습니다.',
  },
  {
    id: 'orchestrator', agent: 'orchestrator', family: 'together', name: '총괄·사건', by: 'AXcellent', Icon: IconBolt, entry: '/orchestrator', paths: ['/orchestrator'],
    desc: '여러 서비스에 걸친 사건을 묶어 조율하고 사람 승인을 받습니다.',
  },
]

/** 서비스를 맡은 AI 이름 (DejaFix 서비스는 없음) */
export const agentName = (s: Service) => AGENTS.find((g) => g.id === s.agent)?.name

/** 서비스를 고르지 않아도 모두 쓰는 화면. 할 일함은 꺼진 서비스로 가던 일이 사람에게 쌓이는 곳이라 어느 서비스를 샀든 보인다 */
export const SHARED_PATHS = ['/handoffs', '/agents/chat', '/agents/office']

export const matchPath = (base: string, path: string) =>
  base === '/' ? path === '/' : path === base || path.startsWith(`${base}/`)

export const serviceOfPath = (path: string) => SERVICES.find((s) => s.paths.some((p) => matchPath(p, path)))

/** 하위 메뉴 중 지금 주소에 가장 길게 맞는 항목 */
export const activeChild = (path: string, items: SubItem[]) =>
  items.filter((c) => matchPath(c.href, path)).sort((a, b) => b.href.length - a.href.length)[0]?.href

// ---------------------------------------------------------------- 회사가 고른 서비스
// 데모라 브라우저에만 기억한다. 메뉴·접근 검사·카탈로그 화면이 같은 값을 보도록 작은 저장소로 둔다.
const KEY = 'axcellent-demo-services'
const ALL = SERVICES.map((s) => s.id)
const read = (): string[] => {
  try {
    const raw = localStorage.getItem(KEY)
    const ids = raw ? (JSON.parse(raw) as string[]) : ALL
    return ids.filter((id) => ALL.includes(id))
  } catch {
    return ALL
  }
}
let enabled = read()
const listeners = new Set<() => void>()

export const getEnabled = () => enabled
export function setEnabled(ids: string[]) {
  enabled = ids
  try {
    localStorage.setItem(KEY, JSON.stringify(ids))
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 전부 켠 상태로 돌아간다 */
  }
  listeners.forEach((l) => l())
}
export const toggleService = (id: string) => setEnabled(enabled.includes(id) ? enabled.filter((x) => x !== id) : [...enabled, id])

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
    return JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
})()
const hiddenListeners = new Set<() => void>()
export const getHidden = () => hidden
export function toggleHidden(id: string) {
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
