import { Fragment, Suspense, lazy, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'

import { AdminShell } from '@/components/admin/AdminShell'
import LoginPage from '@/demo/LoginPage'
import SignupPage from '@/demo/SignupPage'
import PlanPurchase from '@/demo/PlanPurchase'
import { ADMIN_HOME, canOpen, homeOf, useSession } from '@/demo/session'
import { useCompany, useCompanyLoaded } from '@/demo/company'
import Setup from '@/demo/Setup'
import CatalogHome from '@/demo/CatalogHome'
import OperatorLogin from '@/control/OperatorLogin'
import { useHumanTokenSet } from '@/lib/useHumanTokenSet'

// 로그인한 뒤 들어가는 업무 페이지는 따로 받는다(페이지별 코드 나누기). 처음 화면을 열 때 모든 페이지 코드를
// 한꺼번에 받지 않고, 그 페이지를 처음 열 때 그 페이지 코드만 받는다. 로그인 전 화면은 바로 보이게 그대로 둔다
const AgentChat = lazy(() => import('@/pages/AgentChat'))
const AgentOffice = lazy(() => import('@/pages/AgentOffice'))
const Claims = lazy(() => import('@/pages/Claims'))
const Console = lazy(() => import('@/pages/Console'))
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Data = lazy(() => import('@/pages/Data'))
const Finance = lazy(() => import('@/pages/Finance'))
const FinanceGuide = lazy(() => import('@/pages/FinanceGuide'))
const Handoffs = lazy(() => import('@/pages/Handoffs'))
const ArchiveIncidents = lazy(() => import('@/pages/ArchiveIncidents'))
const Hr = lazy(() => import('@/pages/Hr'))
const AgentEvaluation = lazy(() => import('@/pages/AgentEvaluation'))
const OrgChanges = lazy(() => import('@/pages/OrgChanges'))
const Incidents = lazy(() => import('@/pages/Incidents'))
const IncidentDetail = lazy(() => import('@/pages/IncidentDetail'))
const Orchestrator = lazy(() => import('@/pages/Orchestrator'))
const Safety = lazy(() => import('@/pages/Safety'))
const Kb = lazy(() => import('@/pages/Kb'))
const Marketing = lazy(() => import('@/pages/Marketing'))
const PlanningPage = lazy(() => import('@/pages/Planning'))
const DevPage = lazy(() => import('@/pages/Dev'))
const Queue = lazy(() => import('@/pages/Queue'))
const Reviews = lazy(() => import('@/pages/Reviews'))
const Voice = lazy(() => import('@/pages/Voice'))
const Support = lazy(() => import('@/pages/Support'))
const SellerSettlements = lazy(() => import('@/pages/SellerSettlements'))
const Orders = lazy(() => import('@/pages/Orders'))
const ToolConnections = lazy(() => import('@/demo/ToolConnections'))
const CompanyConnect = lazy(() => import('@/demo/CompanyConnect'))
const MembersPage = lazy(() => import('@/demo/MembersPage'))
const ControlCenter = lazy(() => import('@/control/ControlCenter'))

export function App() {
  return (
    <Routes>
      {/* 로그인 전에도 여는 메인: 서비스 제품군 카탈로그 */}
      <Route path="/" element={<CatalogHome />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/plans" element={<PlanPurchase />} />
      {/* 관리자(회사 계정) 가입: 운영자에게 받은 키 → 관리자 계정 → 회사 정보 */}
      <Route path="/setup" element={<Setup />} />
      {/* AXcellent 운영팀 전용 입구 */}
      <Route path="/control/login" element={<OperatorLogin />} />
      <Route path="*" element={<Guarded><Pages /></Guarded>} />
    </Routes>
  )
}

/** 로그인하지 않았으면 메인(카탈로그)으로, 배정되지 않은 서비스 화면이면 내 첫 화면으로 보낸다 */
function Guarded({ children }: { children: ReactNode }) {
  const { account, mode, expired } = useSession()
  const { pathname } = useLocation()
  const company = useCompany()
  const loaded = useCompanyLoaded()
  // 쓰기 버튼(HUMAN_TOKEN_SET 등 132곳)은 렌더할 때 살아 있는 값을 읽는다. 로그인·로그아웃으로 켜짐·꺼짐이 바뀌면
  // 업무 화면을 새로 그려 모든 버튼이 새 값을 보게 한다(바뀔 때만. 같은 사람의 세션 토큰 갱신은 다시 그리지 않는다)
  const tokenSet = useHumanTokenSet()
  // 서버 로그인이 켜져 있는지 묻는 동안은 기다린다(데모 로그인의 ?as=·지난 계정은 꺼져 있다고 확인된 뒤에 들어간다)
  if (!account && mode === 'checking') return <PageLoading />
  // 서버 세션이 만료되면 로그인 화면으로
  if (!account) return <Navigate to={pathname.startsWith('/control') ? '/control/login' : expired ? '/login' : '/'} replace />
  // 연결 회사를 아직 못 읽었으면 기다린다(먼저 보내 버리면 연결된 회사도 새로 열 때마다 회사 연결 화면으로 간다)
  if (!company && !loaded) return <PageLoading />
  if (!canOpen(account, pathname, !!company)) return <Navigate to={homeOf(account)} replace />
  return <Fragment key={tokenSet ? 'human-token' : 'no-human-token'}>{children}</Fragment>
}

function Pages() {
  return (
      <AdminShell>
        <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/inquiry" element={<Dashboard />} />
          <Route path="/console" element={<Console />} />
          <Route path="/claims" element={<Claims />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/settlements" element={<SellerSettlements />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/voice" element={<Voice />} />
          <Route path="/kb" element={<Kb />} />
          <Route path="/finance" element={<Finance />} />
          <Route path="/finance/daily" element={<Finance />} />
          <Route path="/finance/guide" element={<FinanceGuide />} />
          <Route path="/ops" element={<Hr />} />
          <Route path="/archive/incidents" element={<ArchiveIncidents />} />
          <Route path="/agent-eval" element={<AgentEvaluation />} />
          <Route path="/org" element={<OrgChanges />} />
          <Route path="/marketing/*" element={<Marketing />} />
          <Route path="/orchestrator" element={<Orchestrator />} />
          <Route path="/safety" element={<Safety />} />
          <Route path="/data/*" element={<Data />} />
          <Route path="/planning/*" element={<PlanningPage />} />
          <Route path="/dev/*" element={<DevPage />} />
          <Route path="/handoffs" element={<Handoffs />} />
          <Route path="/agents/chat" element={<AgentChat />} />
          <Route path="/agents/office" element={<AgentOffice />} />
          <Route path="/support" element={<Support />} />
          <Route path="/orders" element={<Orders />} />
          {/* 예전 PC·서버 연결 화면은 회원·서버 관리의 DejaFix 연결 탭으로 (관리자 아니면 첫 화면으로 돌아간다) */}
          <Route path="/hosts" element={<Navigate to="/system/members?tab=pcs" replace />} />
          <Route path="/infra/hosts" element={<Navigate to="/system/members?tab=pcs" replace />} />
          <Route path="/infra" element={<Incidents />} />
          <Route path="/infra/:id" element={<IncidentDetail />} />
          {/* 예전 DejaFix 화면 주소(/infra/reports 등)는 현재 장애로 */}
          <Route path="/infra/*" element={<Navigate to="/infra" replace />} />
          {/* PC·서버 연결은 회원·서버 관리의 탭으로 합쳤다 */}
          <Route path="/system/hosts" element={<Navigate to="/system/members?tab=pcs" replace />} />
          <Route path="/system/members" element={<MembersPage />} />
          <Route path="/system/policy" element={<Navigate to="/system/connect" replace />} />
          <Route path="/system/keys" element={<Navigate to="/system/connect" replace />} />
          {/* 서비스 고르기는 없앴다(모든 서비스 필수). 예전 주소는 관리자 첫 화면으로 */}
          <Route path="/system/services" element={<Navigate to={ADMIN_HOME} replace />} />
          <Route path="/system/connect" element={<CompanyConnect />} />
          <Route path="/control/*" element={<ControlCenter />} />
          {/* 회사 서비스 연결은 회사 연결(/system/connect) 화면 아래쪽으로 합쳤다 */}
          <Route path="/system/integrations" element={<Navigate to="/system/connect" replace />} />
          <Route path="/system/tools" element={<ToolConnections />} />
          {/* 예전 연결 화면 주소는 도구 연결로 (노션 계정 연결에서 돌아오는 ?notion=… 은 그대로 넘긴다) */}
          <Route path="/connections" element={<ToTools />} />
          <Route path="/system/devtools" element={<ToTools />} />
          {/* 예전 관리자 대시보드(/admin/*) 주소 호환 */}
          <Route path="/admin/*" element={<LegacyAdminRedirect />} />
          <Route path="*" element={<ToHome />} />
        </Routes>
        </Suspense>
      </AdminShell>
  )
}

/** 페이지 코드를 받는 동안: 사이드 메뉴는 그대로 두고 본문 자리에 뼈대만 보여 준다 */
function PageLoading() {
  const bar = 'animate-pulse rounded bg-ink-100 motion-reduce:animate-none'
  return (
    <div role="status" aria-live="polite" className="space-y-4 px-8 py-6">
      <span className="sr-only">화면을 불러오는 중…</span>
      <div aria-hidden="true" className={`h-7 w-48 ${bar}`} />
      <div aria-hidden="true" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => <div key={i} className={`h-20 rounded-2xl ${bar}`} />)}
      </div>
      <div aria-hidden="true" className={`h-40 rounded-2xl ${bar}`} />
    </div>
  )
}

/** 없는 주소: 내 첫 화면으로 */
function ToHome() {
  const { account } = useSession()
  return <Navigate to={account ? homeOf(account) : '/'} replace />
}

/** /connections?notion=ok → /system/tools?notion=ok */
function ToTools() {
  const { search } = useLocation()
  return <Navigate to={`/system/tools${search}`} replace />
}

/** /admin/console?id=3 → /console?id=3 */
function LegacyAdminRedirect() {
  const { pathname, search } = useLocation()
  return <Navigate to={(pathname.replace(/^\/admin/, '') || '/') + search} replace />
}
