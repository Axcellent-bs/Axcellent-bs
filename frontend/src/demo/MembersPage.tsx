import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { useCompany } from './company'
import InfraHosts from './InfraHosts'
import { ipMatches, linkEmail, linkIp, setProfile, unlinkEmail, unlinkIp, useLinks, useProfiles, validRule } from './members'
import { DeptPicker } from './DeptPicker'
import { customerAccounts, kindOf, type Account } from './session'
import { privateIp, useHosts } from './store'

// 회원·서버 관리 (관리자 /system/members). 회사 사람과 PC를 한 화면 탭으로 관리한다.
// - 회원: 계정 연결(이메일)·부서(선택, 표시·조직도용)·확인 권한·쓰는 PC를 한 표에서
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

export default function MembersPage() {
  const company = useCompany()
  const links = useLinks()
  const [hosts] = useHosts()
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.some((t) => t.id === params.get('tab')) ? (params.get('tab') as Tab) : 'members'
  const accounts = customerAccounts()

  return (
    <div className="space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-500">시스템 관리</p>
          <h1 className="text-2xl font-semibold">회원·서버 관리</h1>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {company && <span className="chip bg-ink-100 text-ink-700">{company.name}</span>}
          <span className="chip bg-brand-50 text-brand-700">회원 {accounts.length}</span>
          <span className="chip bg-brand-50 text-brand-700">PC·서버 {hosts.length}</span>
          <span className="chip bg-brand-50 text-brand-700">접속 허용 IP {links.ips.length}</span>
        </div>
      </header>

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="회원·서버 관리">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
            onClick={() => setParams(t.id === 'members' ? {} : { tab: t.id }, { replace: true })}
            className={`rounded-full px-4 py-1.5 text-sm font-medium ${tab === t.id ? 'bg-accent text-white' : 'bg-ink-50 text-ink-700 hover:bg-ink-100'}`}>{t.label}</button>
        ))}
      </div>

      {tab === 'members' && <Members />}
      {tab === 'pcs' && <InfraHosts embedded />}
      {tab === 'access' && <Access />}
    </div>
  )
}

/** 회원 한 표: 연결 · 부서 · 확인 권한 · 쓰는 PC */
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
    <section className="card space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">회원</h2>
          <p className="mt-1 text-xs text-ink-500">이메일을 연결하면 가입 전이어도 미리 회사 회원으로 넣어 둬요. 부서는 고르지 않아도 되고, 표시와 조직도에만 써요.</p>
        </div>
        <form onSubmit={addEmail} className="flex gap-2">
          <input className="field w-64" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="회원 이메일" aria-label="연결할 회원 이메일" />
          <button className="btn-primary px-4">연결</button>
        </form>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="overflow-x-auto rounded-xl border border-ink-100">
        <table className="w-full whitespace-nowrap text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">이름</th>
              <th className="px-4 py-2.5 font-medium">구분</th>
              <th className="px-4 py-2.5 font-medium">연결</th>
              <th className="px-4 py-2.5 font-medium">부서 (선택)</th>
              <th className="px-4 py-2.5 font-medium">확인 권한</th>
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
                  <td className="px-4 py-2.5">{m.name}<p className="font-mono text-xs text-ink-500">{m.email}</p></td>
                  <td className="px-4 py-2.5"><span className="chip bg-ink-100 text-ink-700">{kindOf(m)}</span></td>
                  <td className="px-4 py-2.5">
                    {!member ? <span className="text-xs text-ink-500">–</span>
                      : byEmail ? <span className="chip bg-emerald-50 text-emerald-700">계정 연결</span>
                      : <span className="chip bg-ink-100 text-ink-500">연결 전</span>}
                  </td>
                  <td className="px-4 py-2.5"><DeptPicker size="sm" label={`${m.name} 부서`} value={m.dept ?? ''} onChange={(d) => setProfile(m.email, { dept: d })} allowNone /></td>
                  <td className="px-4 py-2.5"><ApproverSwitch account={m} /></td>
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
                <td className="px-4 py-2.5"><span className="chip bg-ink-100 text-ink-700">일반회원</span></td>
                <td className="px-4 py-2.5"><span className="chip bg-emerald-50 text-emerald-700">계정 연결</span></td>
                <td className="px-4 py-2.5 text-xs text-ink-500">가입 뒤 정해요</td>
                <td className="px-4 py-2.5" />
                <td className="px-4 py-2.5 text-xs text-ink-500">없음</td>
                <td className="px-4 py-2.5 text-right"><button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => unlinkEmail(e)}>해제</button></td>
              </tr>
            ))}
            {!accounts.length && !pending.length && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-ink-500">가입한 회원이 없어요</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-ink-500">쓰는 PC는 PC·서버 탭에서 PC마다 쓰는 사람을 고르면 여기에 나와요.</p>
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
    <section className="card space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">접속 허용</h2>
          <p className="mt-1 text-xs text-ink-500">여기 넣은 사내 IP나 대역의 PC에서 가입·로그인하면 회사 회원이 돼요.</p>
        </div>
        <form onSubmit={addIp} className="flex gap-2">
          <input className="field w-64 font-mono" value={rule} onChange={(e) => setRule(e.target.value)} placeholder="192.168.10.31 또는 /24" aria-label="허용할 IP 또는 대역" />
          <button className="btn-primary px-4">허용</button>
        </form>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {company && !links.ips.includes(company.network) && (
        <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => linkIp(company.network)}>사내망 전체 {company.network} 허용</button>
      )}
      <ul className="grid gap-2 md:grid-cols-2">
        {links.ips.map((r) => (
          <li key={r} className="flex items-center gap-3 rounded-xl border border-ink-100 bg-app px-4 py-2.5 text-sm">
            <span className="font-mono">{r}</span>
            <span className="text-xs text-ink-500">사내 PC {pcsOf(r).length}대</span>
            <button type="button" className="btn-quiet ml-auto px-2 py-1 text-xs" onClick={() => unlinkIp(r)}>해제</button>
          </li>
        ))}
        {!links.ips.length && <li className="text-sm text-ink-500">허용한 IP가 없어요</li>}
      </ul>
    </section>
  )
}
