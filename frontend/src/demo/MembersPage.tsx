import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { useCompany } from './company'
import InfraHosts from './InfraHosts'
import { READER_AREAS, ipMatches, linkEmail, linkIp, setProfile, unlinkEmail, unlinkIp, useLinks, useProfiles, validRule } from './members'
import { DeptPicker } from './DeptPicker'
import { customerAccounts, kindOf, type Account } from './session'
import { privateIp, useHosts } from './store'
import { More, PageHead, SectionHead, StatusChip, Tabs, Tiles } from '@/components/connect/parts'

// 회원·서버 관리 (관리자 /system/members). 회사 사람과 PC를 한 화면 탭으로 관리한다.
// - 회원: 계정 연결(이메일)·부서(선택, 표시·조직도용)·확인 권한·열람 담당(인사·재무 AI 대화 요약)·쓰는 PC를 한 표에서
// - PC·서버: 조직도와 PC 연결 (InfraHosts). 예전 /system/hosts 주소는 이 탭으로 온다
// - 접속 허용: 사내 PC IP나 대역. 그 PC에서 가입·로그인하면 회사 회원이 된다
// 계정 연결과 접속 허용 중 하나라도 맞으면 그 회원은 회사 회원이다.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const TABS = [
  { id: 'members', label: '회원' },
  { id: 'pcs', label: 'PC·서버' },
  { id: 'access', label: '접속 허용' },
] as const
type Tab = (typeof TABS)[number]['id']

/** 확인 권한 켜고 끄기 */
function ApproverSwitch({ account }: { account: Account }) {
  const on = !!account.approver
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={`${account.name} 확인 권한`}
      onClick={() => setProfile(account.email, { approver: !on })}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${on ? 'bg-brand-600' : 'bg-ink-100'}`}>
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  )
}

/** 열람 담당 고르기: 인사·재무 AI의 다른 사람 대화 요약을 볼 사람 */
function ReaderPicker({ account }: { account: Account }) {
  const on = account.readers ?? []
  return (
    <div className="flex gap-1" role="group" aria-label={`${account.name} 열람 담당`}>
      {READER_AREAS.map((r) => {
        const sel = on.includes(r.id)
        return (
          <button key={r.id} type="button" aria-pressed={sel}
            onClick={() => setProfile(account.email, { readers: sel ? on.filter((x) => x !== r.id) : [...on, r.id] })}
            className={`chip ${sel ? 'bg-brand-600 text-white' : 'bg-ink-50 text-ink-500 hover:bg-ink-100'}`}>{r.label}</button>
        )
      })}
    </div>
  )
}

export default function MembersPage() {
  const company = useCompany()
  const links = useLinks()
  const [hosts] = useHosts()
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.some((t) => t.id === params.get('tab')) ? (params.get('tab') as Tab) : 'members'
  const accounts = customerAccounts()

  const online = hosts.filter((h) => h.status === 'online').length
  const approvers = accounts.filter((a) => a.approver).length
  const counts: Record<Tab, number> = { members: accounts.length, pcs: hosts.length, access: links.ips.length }

  return (
    <div className="space-y-8 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="회원·서버 관리">
        {company?.name ?? '회사'}의 회원과 PC를 관리해요. 이메일을 연결하거나 허용한 사내 IP에서 접속하면 회사 회원이 돼요.
      </PageHead>

      <Tiles label="회원·서버 요약" items={[
        { name: '회원', value: `${accounts.length}명`, tone: 'ok', note: `계정 연결 ${links.emails.length}개` },
        { name: '확인 권한', value: `${approvers}명`, tone: approvers ? 'ok' : 'wait', note: approvers ? 'AI 제안을 승인할 수 있어요' : '승인할 사람이 없어요' },
        {
          name: 'PC·서버', value: `${online} / ${hosts.length}대`,
          tone: hosts.length === 0 ? 'off' : online === hosts.length ? 'ok' : 'wait',
          note: hosts.length && online < hosts.length ? `${hosts.length - online}대 응답 없음` : '모두 응답해요',
        },
        { name: '접속 허용', value: `${links.ips.length}개`, tone: links.ips.length ? 'ok' : 'off', note: '사내 IP·대역' },
      ]} />

      <Tabs label="회원·서버 관리" value={tab} items={TABS.map((t) => ({ ...t, count: counts[t.id] }))}
        onPick={(id) => setParams(id === 'members' ? {} : { tab: id }, { replace: true })} />

      {tab === 'members' && <Members />}
      {tab === 'pcs' && <InfraHosts embedded />}
      {tab === 'access' && <Access />}
    </div>
  )
}

/** 회원 한 표: 연결 · 부서 · 확인 권한 · 열람 담당 · 쓰는 PC */
function Members() {
  const links = useLinks()
  useProfiles() // 부서·확인 권한을 바꾸면 다시 그린다
  const [hosts] = useHosts()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)

  const accounts = customerAccounts()
  const known = new Set(accounts.map((m) => m.email))
  const pending = links.emails.filter((e) => !known.has(e))
  const pcsOf = (e: string) => hosts.filter((h) => h.owner === e)

  const addEmail = (e: FormEvent) => {
    e.preventDefault()
    if (!EMAIL.test(email.trim())) return setError('이메일 형식이 아닙니다.')
    linkEmail(email)
    setEmail('')
    setError(null)
  }

  return (
    <section className="space-y-4">
      <SectionHead title="회원" desc="이메일을 연결하면 가입 전이어도 미리 회사 회원으로 넣어 둬요."
        right={
          <form onSubmit={addEmail} className="flex gap-2">
            <input className="field w-64" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="회원 이메일" aria-label="연결할 회원 이메일" />
            <button className="btn-primary px-4">회원 연결</button>
          </form>
        } />
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div className="card overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">이름</th>
              <th className="px-4 py-2.5 font-medium">구분</th>
              <th className="px-4 py-2.5 font-medium">연결</th>
              <th className="px-4 py-2.5 font-medium">부서 (선택)</th>
              <th className="px-4 py-2.5 font-medium">확인 권한</th>
              <th className="px-4 py-2.5 font-medium">열람 담당</th>
              <th className="px-4 py-2.5 font-medium">쓰는 PC</th>
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {accounts.map((m) => {
              const member = kindOf(m) === '일반회원'
              const byEmail = links.emails.includes(m.email)
              const pcs = pcsOf(m.email)
              return (
                <tr key={m.id}>
                  <td className="px-4 py-2.5"><p className="font-medium text-ink-900">{m.name}</p><p className="font-mono text-xs text-ink-500">{m.email}</p></td>
                  <td className="px-4 py-2.5 text-ink-700">{kindOf(m)}</td>
                  <td className="px-4 py-2.5">
                    {!member ? <span className="text-xs text-ink-500">–</span>
                      : byEmail ? <StatusChip tone="ok">계정 연결</StatusChip>
                      : <StatusChip tone="off">연결 전</StatusChip>}
                  </td>
                  <td className="px-4 py-2.5"><DeptPicker size="sm" label={`${m.name} 부서`} value={m.dept ?? ''} onChange={(d) => setProfile(m.email, { dept: d })} allowNone /></td>
                  <td className="px-4 py-2.5"><ApproverSwitch account={m} /></td>
                  <td className="px-4 py-2.5">{m.admin ? <span className="text-xs text-ink-500">관리자는 모두 봐요</span> : <ReaderPicker account={m} />}</td>
                  <td className="px-4 py-2.5 text-xs">
                    {pcs.length ? pcs.map((h) => <span key={h.id} className="mr-1 font-mono">{h.hostname}</span>) : <span className="text-ink-500">없음</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {member && (byEmail
                      ? <button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => unlinkEmail(m.email)}>해제</button>
                      : <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => linkEmail(m.email)}>연결</button>)}
                  </td>
                </tr>
              )
            })}
            {pending.map((e) => (
              <tr key={e}>
                <td className="px-4 py-2.5 text-ink-500">가입 전<p className="font-mono text-xs">{e}</p></td>
                <td className="px-4 py-2.5 text-ink-700">일반회원</td>
                <td className="px-4 py-2.5"><StatusChip tone="wait">가입 전</StatusChip></td>
                <td className="px-4 py-2.5 text-xs text-ink-500">가입 뒤 정해요</td>
                <td className="px-4 py-2.5" />
                <td className="px-4 py-2.5 text-xs text-ink-500">없음</td>
                <td className="px-4 py-2.5 text-right"><button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => unlinkEmail(e)}>해제</button></td>
              </tr>
            ))}
            {!accounts.length && !pending.length && (
              <tr><td colSpan={8} className="px-4 py-6 text-center text-ink-500">가입한 회원이 없어요</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <More summary="칸마다 무슨 뜻인가요">
        <p>부서는 고르지 않아도 되고, 표시와 조직도에만 써요.</p>
        <p>확인 권한이 있으면 AI가 제안한 일을 승인할 수 있어요.</p>
        <p>AI 대화방의 다른 사람 대화 요약은 모두가 봐요. 인사·재무 AI만 관리자와 열람 담당으로 고른 사람이 봐요.</p>
        <p>쓰는 PC는 PC·서버 탭에서 PC마다 쓰는 사람을 고르면 여기에 나와요.</p>
      </More>
    </section>
  )
}

/** 접속 허용: 사내 PC IP·대역 */
function Access() {
  const company = useCompany()
  const links = useLinks()
  const [hosts] = useHosts()
  const [rule, setRule] = useState('')
  const [error, setError] = useState<string | null>(null)

  const addIp = (e: FormEvent) => {
    e.preventDefault()
    const r = rule.trim()
    if (!validRule(r) || !privateIp(r.split('/')[0])) return setError('사내 IP(192.168.10.31)나 대역(192.168.10.0/24)만 넣을 수 있어요.')
    linkIp(r)
    setRule('')
    setError(null)
  }
  const pcsOf = (r: string) => hosts.filter((h) => ipMatches(h.ip, r))

  return (
    <section className="space-y-4">
      <SectionHead title="접속 허용" desc="여기 넣은 사내 IP나 대역의 PC에서 가입·로그인하면 회사 회원이 돼요."
        right={
          <form onSubmit={addIp} className="flex gap-2">
            <input className="field w-64 font-mono" value={rule} onChange={(e) => setRule(e.target.value)} placeholder="192.168.10.31 또는 /24" aria-label="허용할 IP 또는 대역" />
            <button className="btn-primary px-4">허용</button>
          </form>
        } />
      {error && <p className="text-sm text-red-700">{error}</p>}
      {company && !links.ips.includes(company.network) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-ink-300 px-4 py-3 text-sm">
          <span className="text-ink-700">사내망 전체(<span className="font-mono">{company.network}</span>)를 한 번에 허용할 수 있어요.</span>
          <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => linkIp(company.network)}>사내망 전체 허용</button>
        </div>
      )}
      <ul className="card divide-y divide-ink-100">
        {links.ips.map((r) => (
          <li key={r} className="flex items-center gap-4 px-5 py-3 text-sm">
            <span className="font-mono font-medium text-ink-900">{r}</span>
            <span className="text-xs text-ink-500">이 범위의 사내 PC {pcsOf(r).length}대</span>
            <button type="button" className="btn-quiet ml-auto px-2 py-1 text-xs" onClick={() => unlinkIp(r)}>해제</button>
          </li>
        ))}
        {!links.ips.length && <li className="px-5 py-6 text-center text-sm text-ink-500">허용한 IP가 없어요. 위에서 사내 IP나 대역을 넣어 주세요.</li>}
      </ul>
    </section>
  )
}
