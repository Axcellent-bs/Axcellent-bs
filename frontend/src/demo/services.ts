// 서비스 카탈로그. 부서를 코드에 박지 않으려고 부서 묶음을 없애고, 부서 안에 있던 기능을 서비스 하나하나로 꺼냈다.
// 모든 서비스는 필수라 회사가 고르지 않는다(2026-11-18). 서비스는 시스템 관리자 메뉴에 전부 보인다(session.tsx).
// 화면은 예전 캣북스 대시보드의 AI 화면을 옮겨 온 것이다. paths가 그 서비스에 속한 화면 주소, agent가 맡은 AI다.
import type { ComponentType } from 'react'
import {
  IconBolt, IconBook, IconBox, IconCart, IconChart, IconClock, IconCode, IconLock, IconMegaphone, IconOffice, IconPlan, IconReceipt, IconShield, IconStar, IconUser, IconUsers,
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
 * AXcellent가 관리하는 AI 9개와 안전 처리 로그. 예전 캣북스 대시보드(ax-frontend dashboard-web)에 있던 AI를 모두 이리로 옮겼다.
 * id는 ax-backend .github/team.yml 부서 id, backend는 화면이 부르는 서버(src/lib/env.ts).
 * 왼쪽 메뉴는 이 순서대로 AI가 상위 탭이고, 그 AI가 맡은 서비스가 하위 탭이다 (AdminShell).
 * 안전 처리 로그(safety)는 모든 AI의 상태 변경을 execute()로 받아 판단하므로 화면(/safety)을 둔다. 회사 기억(memory)은 뒤에서 도는 서버라 넣지 않는다. 인프라는 DejaFix가 맡는다.
 */
export type AgentId = 'orchestrator' | 'safety' | 'support' | 'finance' | 'data' | 'archive' | 'hr' | 'marketing' | 'planning' | 'dev'
export const AGENTS: { id: AgentId; name: string; Icon: ComponentType<{ size?: number }>; backend: string }[] = [
  { id: 'orchestrator', Icon: IconBolt, name: '총괄 AI', backend: 'orchestrator (8000) · 판매 서버 관리 읽기 중계 (dashboard-api 8020)' },
  { id: 'safety', Icon: IconLock, name: '안전 처리 로그', backend: 'safety (8001)' },
  { id: 'archive', Icon: IconBook, name: '아카이브 AI', backend: 'decisions (8114) · dashboard-api (8020)' },
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

const DEJAFIX_LOGO = `${import.meta.env.BASE_URL}dejafix-logo.webp` // 가장 크게 26px로 그린다 → 64px WebP (예전 256px PNG 25KB)

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
    id: 'claims', agent: 'support', family: 'customer', name: '클레임·보상', by: 'AXcellent', Icon: IconReceipt, entry: '/claims', paths: ['/claims', '/queue', '/settlements'],
    desc: '환불·교환 신청을 확인하고 보상을 규칙대로 처리합니다. 판매자 정산(정산금 계산·지급 승인 요청)도 여기서 합니다.',
    children: [
      { href: '/claims', label: '클레임' },
      { href: '/queue', label: '보상 큐' },
      { href: '/settlements', label: '판매자 정산' },
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
    // 화면 안 전환(pages/Marketing.tsx)을 사이드바 하위 탭으로 연다. 옛 주소 /marketing 은 첫 탭이다
    children: [
      { href: '/marketing', label: '장애·공지' },
      { href: '/marketing/campaign', label: '캠페인·쿠폰' },
      { href: '/marketing/briefs', label: '받은 기획안' },
      { href: '/marketing/sns', label: 'SNS·미디어' },
    ],
  },
  {
    id: 'finance', agent: 'finance', family: 'backoffice', name: '재무 관리', by: 'AXcellent', Icon: IconShield, entry: '/finance', paths: ['/finance'],
    desc: '결산·대사·정산과 장애 보상 장부, 이벤트 후 감사를 맡습니다.',
    // 화면 안 전환(pages/Finance.tsx)을 사이드바 하위 탭으로 연다. ①~⑥ 업무 탭은 평상시 화면 안에 둔다
    children: [
      { href: '/finance', label: '재무 장애 이슈' },
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
      { href: '/data/sales', label: '매출 분석' },
      { href: '/data/segments', label: '고객·시장 분석' },
    ],
  },
  {
    id: 'decisions', agent: 'archive', family: 'backoffice', name: '장애·업무 기록', by: 'AXcellent', Icon: IconBook, entry: '/archive/incidents', paths: ['/ops', '/archive'],
    desc: '장애 기록(총괄 AI가 처리한 이슈와 해결 내용)과 업무 기록(회사의 결정, 기각된 안과 겹치는지 확인)을 남기고 찾아봅니다.',
    children: [
      { href: '/archive/incidents', label: '장애 기록' },
      { href: '/ops', label: '업무 기록' },
    ],
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
    id: 'orchestrator', agent: 'orchestrator', family: 'together', name: '총괄 AI·사건', by: 'AXcellent', Icon: IconBolt, entry: '/orchestrator', paths: ['/orchestrator'],
    desc: '여러 서비스에 걸친 사건을 묶어 조율하고 사람 승인을 받습니다.',
  },
  {
    id: 'safety', agent: 'safety', family: 'together', name: '안전 처리 로그', by: 'AXcellent', Icon: IconLock, entry: '/safety', paths: ['/safety'],
    desc: '모든 AI의 돈·게시·배포 요청에 등급을 매겨 실행하거나 사람 결정으로 보류합니다.',
  },
]

/** 서비스를 맡은 AI 이름 (DejaFix 서비스는 없음) */
export const agentName = (s: Service) => AGENTS.find((g) => g.id === s.agent)?.name

/** 어느 서비스에도 속하지 않아 모든 계정이 여는 화면. 할 일함은 AI가 사람에게 넘긴 일이 쌓이는 곳이다.
 *  도구 연결(/system/tools)은 시스템 관리 아래지만 모두 연다(회사 공용 연결은 관리자만 바꾼다).
 *  /connections·/system/devtools는 도구 연결로 넘기는 예전 주소 (노션 계정 연결이 /connections?notion=…로 돌아온다) */
export const SHARED_PATHS = ['/handoffs', '/agents/chat', '/agents/office', '/system/tools', '/connections', '/system/devtools']

export const matchPath = (base: string, path: string) =>
  base === '/' ? path === '/' : path === base || path.startsWith(`${base}/`)

export const serviceOfPath = (path: string) => SERVICES.find((s) => s.paths.some((p) => matchPath(p, path)))

/** 하위 메뉴 중 지금 주소에 가장 길게 맞는 항목 */
export const activeChild = (path: string, items: SubItem[]) =>
  items.filter((c) => matchPath(c.href, path)).sort((a, b) => b.href.length - a.href.length)[0]?.href

// ---------------------------------------------------------------- 서비스는 모두 필수
// 회사가 서비스를 골라 끄거나, 운영팀이 카탈로그에서 내리지 않는다(2026-11-18 결정).
// 꺼진 서비스를 건너뛰는 흐름을 따로 만들지 않으려고 화면은 늘 전체 서비스를 쓴다.
// 백엔드 AX_ENABLED_SERVICES는 개발할 때 서버 일부만 띄우는 용도로만 남는다(run_all.sh --only).

