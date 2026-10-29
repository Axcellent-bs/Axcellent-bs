import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'

import { AdminShell } from '@/components/admin/AdminShell'
import AgentChat from '@/pages/AgentChat'
import AgentOffice from '@/pages/AgentOffice'
import Claims from '@/pages/Claims'
import Console from '@/pages/Console'
import Dashboard from '@/pages/Dashboard'
import Data from '@/pages/Data'
import Finance from '@/pages/Finance'
import FinanceGuide from '@/pages/FinanceGuide'
import Handoffs from '@/pages/Handoffs'
import Hr from '@/pages/Hr'
import Incidents from '@/pages/Incidents'
import IncidentDetail from '@/pages/IncidentDetail'
import Orchestrator from '@/pages/Orchestrator'
import Kb from '@/pages/Kb'
import Marketing from '@/pages/Marketing'
import PlanningPage from '@/pages/Planning'
import DevPage from '@/pages/Dev'
import Queue from '@/pages/Queue'
import Reviews from '@/pages/Reviews'
import Support from '@/pages/Support'
import InfraHosts from '@/demo/InfraHosts'
import Integrations from '@/demo/Integrations'
import DevTools from '@/demo/DevTools'
import LoginPage from '@/demo/LoginPage'
import SignupPage from '@/demo/SignupPage'
import PlanPurchase from '@/demo/PlanPurchase'
import { canOpen, homeOf, useSession } from '@/demo/session'
import { useEnabledServices } from '@/demo/services'
import { useCompany } from '@/demo/company'
import ServiceCatalog from '@/demo/ServiceCatalog'
import CompanyConnect from '@/demo/CompanyConnect'
import MembersPage from '@/demo/MembersPage'
import Setup from '@/demo/Setup'
import CatalogHome from '@/demo/CatalogHome'
import ControlCenter from '@/control/ControlCenter'
import OperatorLogin from '@/control/OperatorLogin'

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

/** 로그인하지 않았으면 메인(카탈로그)으로, 배정되지 않았거나 회사가 끈 서비스 화면이면 내 첫 화면으로 보낸다 */
function Guarded({ children }: { children: ReactNode }) {
  const { account } = useSession()
  const { pathname } = useLocation()
  const enabled = useEnabledServices()
  const company = useCompany()
  if (!account) return <Navigate to={pathname.startsWith('/control') ? '/control/login' : '/'} replace />
  if (!canOpen(account, pathname, enabled, !!company)) return <Navigate to={homeOf(account, enabled)} replace />
  return <>{children}</>
}

function Pages() {
  return (
      <AdminShell>
        <Routes>
          <Route path="/inquiry" element={<Dashboard />} />
          <Route path="/console" element={<Console />} />
          <Route path="/claims" element={<Claims />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/kb" element={<Kb />} />
          <Route path="/finance" element={<Finance />} />
          <Route path="/finance/guide" element={<FinanceGuide />} />
          <Route path="/hr" element={<Hr part="hr" />} />
          <Route path="/ops" element={<Hr part="ops" />} />
          <Route path="/marketing" element={<Marketing />} />
          <Route path="/orchestrator" element={<Orchestrator />} />
          <Route path="/data" element={<Data />} />
          <Route path="/planning/*" element={<PlanningPage />} />
          <Route path="/dev/*" element={<DevPage />} />
          <Route path="/handoffs" element={<Handoffs />} />
          <Route path="/agents/chat" element={<AgentChat />} />
          <Route path="/agents/office" element={<AgentOffice />} />
          <Route path="/support" element={<Support />} />
          <Route path="/hosts" element={<InfraHosts />} />
          <Route path="/infra/hosts" element={<Navigate to="/hosts" replace />} />
          <Route path="/infra" element={<Incidents />} />
          <Route path="/infra/:id" element={<IncidentDetail />} />
          {/* 예전 DejaFix 화면 주소(/infra/reports 등)는 현재 장애로 */}
          <Route path="/infra/*" element={<Navigate to="/infra" replace />} />
          <Route path="/system/hosts" element={<InfraHosts />} />
          <Route path="/system/members" element={<MembersPage />} />
          <Route path="/system/services" element={<ServiceCatalog />} />
          <Route path="/system/connect" element={<CompanyConnect />} />
          <Route path="/control/*" element={<ControlCenter />} />
          <Route path="/system/integrations" element={<Integrations />} />
          <Route path="/system/devtools" element={<DevTools />} />
          {/* 예전 관리자 대시보드(/admin/*) 주소 호환 */}
          <Route path="/admin/*" element={<LegacyAdminRedirect />} />
          <Route path="*" element={<ToHome />} />
        </Routes>
      </AdminShell>
  )
}

/** 없는 주소: 내 첫 화면으로 */
function ToHome() {
  const { account } = useSession()
  return <Navigate to={account ? homeOf(account) : '/'} replace />
}

/** /admin/console?id=3 → /console?id=3 */
function LegacyAdminRedirect() {
  const { pathname, search } = useLocation()
  return <Navigate to={(pathname.replace(/^\/admin/, '') || '/') + search} replace />
}
