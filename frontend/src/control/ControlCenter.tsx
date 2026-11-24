// AXcellent 관제 센터 (AXcellent 운영팀 전용, 고객사 계정은 못 연다). 고객사에 설치된 AXcellent가 보내는 상태 신호만 모아 본다.
// 탭: 전체 현황 · 고객사 · 서비스 관리 · 장애·사건 · AI 품질 · 비용·사용량 · 연결 키 발급 · 계정.
// 실제 출처가 있는 값만 그린다: 연결된 고객사는 연결 키 목록(dashboard-api `/connection-keys`, 운영자는 모든 키를 본다).
// 상태·버전·사건·품질·비용은 고객사 설치의 상태 신호가 아직 없어 "준비 중"이다(데이터를 지어내지 않는다).
import type { ReactNode } from 'react'
import { useLocation } from 'react-router'
import KeyIssue from './KeyIssue'
import Accounts from './Accounts'
import ServiceOps from './ServiceOps'
import { PreparingChip, PreparingNotice } from '@/components/Preparing'
import { useCompany, useKeys, type IssuedKey } from '@/demo/company'
import { ADMIN_HOME } from '@/demo/session'
import { startSupport } from '@/demo/support'
import { useRouter } from '@/lib/nav'

const SIGNAL = '고객사 설치의 상태 신호'

function Page({ title, desc, children }: { title: string; desc: string; children: ReactNode }) {
  return (
    <div className="space-y-6 px-8 pb-12">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-ink-500">관제 센터</p>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="mt-1 text-sm text-ink-500">{desc}</p>
        </div>
        <span className="chip bg-ink-100 text-ink-700" title="업무 데이터(주문·문의·인사 기록)는 받지 않아요">받는 범위: 상태 신호만</span>
      </header>
      {children}
    </div>
  )
}

function Tile({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="card px-5 py-4">
      <p className="text-xs text-ink-500">{label}</p>
      <div className="mt-1 text-3xl font-bold tabular-nums text-ink-900">{value}</div>
      {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
    </div>
  )
}

const Soon = () => <span className="text-base font-normal"><PreparingChip /></span>

/** 연결 키로 연결된 고객사 (키 하나 = 회사 하나). 연결 정보가 없는 키는 뺀다 */
function connected(keys: IssuedKey[]) {
  return keys.flatMap((k) => (k.status === 'connected' && k.company ? [{ key: k, company: k.company }] : []))
}

// ---------------------------------------------------------------- 탭
function Overview() {
  const rows = connected(useKeys())
  return (
    <Page title="전체 현황" desc="고객사에 설치된 AXcellent가 잘 돌고 있는지 한눈에 봐요.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="연결된 고객사" value={rows.length} note="연결 키로 연결한 회사" />
        <Tile label="대응 중인 사건" value={<Soon />} />
        <Tile label="업데이트가 밀린 고객사" value={<Soon />} />
        <Tile label="오늘 AI 자동 처리율" value={<Soon />} />
      </div>
      <PreparingNotice what="고객사 상태·사건·버전" source={SIGNAL} />
    </Page>
  )
}

function Companies() {
  const current = useCompany()
  const rows = connected(useKeys())
  const router = useRouter()
  // 지원 모드로 들어가기: 지금 이 화면이 연결된 회사만 된다
  const enter = () => {
    if (!current) return
    startSupport(current)
    router.push(ADMIN_HOME)
  }
  return (
    <Page title="고객사" desc="연결 키로 연결한 고객사예요. 상태·버전·마지막 신호는 상태 신호가 연결되면 나와요.">
      <p className="bg-brand-50 px-4 py-3 text-sm text-brand-700">
        연결은 고객사 관리자가 발급받은 키로 관리자 가입을 하면서 해요(연결 키 발급 탭). 지금 이 화면에 연결된 회사: <b>{current?.name ?? '없음'}</b>
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">고객사</th>
              <th className="px-3 py-3 font-medium">업종</th>
              <th className="px-3 py-3 font-medium">연결 키</th>
              <th className="px-3 py-3 font-medium">상태·버전</th>
              <th className="px-5 py-3"><span className="sr-only">지원 모드</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {rows.map(({ key, company }) => {
              const here = !!current && current.id === company.id
              return (
                <tr key={key.id} className={here ? 'bg-brand-50' : ''}>
                  <td className="px-5 py-3 font-medium">{company.name}</td>
                  <td className="px-3 py-3 text-ink-700">{company.industry || '—'}</td>
                  <td className="px-3 py-3 font-mono text-xs">{key.key}</td>
                  <td className="px-3 py-3"><PreparingChip /></td>
                  <td className="px-5 py-3 text-right">
                    {here && (
                      <button type="button" className="btn-primary px-3 py-1 text-xs" onClick={enter} title="보기 전용으로 이 고객사 화면에 들어가요">들어가기</button>
                    )}
                  </td>
                </tr>
              )
            })}
            {!rows.length && <tr><td colSpan={5} className="px-5 py-6 text-center text-ink-500">연결된 고객사가 없어요. 연결 키 발급 탭에서 키를 발급해 고객사에 전해 주세요.</td></tr>}
          </tbody>
        </table>
      </div>
    </Page>
  )
}

function Incidents() {
  return (
    <Page title="장애·사건" desc="고객사에서 올라온 사건을 원인 쪽으로 나눠요. 우리 쪽이면 고치고, 고객사·외부 쪽이면 안내해요.">
      <PreparingNotice what="고객사 사건" source={SIGNAL} />
      <p className="text-xs text-ink-500">고객사 한 곳의 장애는 그 고객사의 장애 대응 화면(DejaFix)에서 봐요. 관제 센터는 여러 고객사를 모아 보는 곳이에요.</p>
    </Page>
  )
}

function Quality() {
  return (
    <Page title="AI 품질" desc="AI가 실수하고 있지 않은지 봐요. 자동 처리율이 낮거나 막힌 건이 늘면 기준을 다시 봐요.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="사람 승인" value={<Soon />} />
        <Tile label="안전 관문에서 막힘" value={<Soon />} />
        <Tile label="되돌림(롤백)" value={<Soon />} />
        <Tile label="AI 실수 신고" value={<Soon />} />
      </div>
      <PreparingNotice what="서비스별 자동 처리율" source={SIGNAL} />
    </Page>
  )
}

function Cost() {
  return (
    <Page title="비용·사용량" desc="LLM 호출 수와 비용이에요. 서비스마다 맞는 모델을 써서 비용을 낮춰요.">
      <div className="grid gap-3 sm:grid-cols-3">
        <Tile label="오늘 비용" value={<Soon />} note="전체 고객사 합계" />
        <Tile label="7일 전보다" value={<Soon />} />
        <Tile label="오늘 LLM 호출" value={<Soon />} note="전체 고객사 합계" />
      </div>
      <PreparingNotice what="고객사별 LLM 비용·호출" source={SIGNAL} />
    </Page>
  )
}

export default function ControlCenter() {
  const { pathname } = useLocation()
  const tab = pathname.replace(/^\/control\/?/, '').split('/')[0]
  if (tab === 'companies') return <Companies />
  if (tab === 'incidents') return <Incidents />
  if (tab === 'quality') return <Quality />
  if (tab === 'cost') return <Cost />
  if (tab === 'keys') return <KeyIssue />
  if (tab === 'accounts') return <Accounts />
  if (tab === 'services') return <ServiceOps />
  return <Overview />
}
