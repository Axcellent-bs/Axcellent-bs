import { useState, type FormEvent } from 'react'
import { useCompany } from './company'
import { ipMatches, linkEmail, linkIp, setProfile, unlinkEmail, unlinkIp, useLinks, useProfiles, validRule } from './members'
import { DeptPicker } from './DeptPicker'
import { customerAccounts, kindOf, type Account } from './session'
import { privateIp, useHosts } from './store'

// 회원 연결 (관리자 /system/members). 일반회원을 회사 계정에 묶는다.
// 계정 연결은 이메일로, IP 연결은 사내 PC IP나 대역으로. 둘 중 하나라도 맞으면 그 회원은 회사 회원이다.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

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
  useProfiles() // 부서·확인 권한을 바꾸면 다시 그린다
  const [hosts] = useHosts()
  const [email, setEmail] = useState('')
  const [rule, setRule] = useState('')
  const [error, setError] = useState<{ at: 'email' | 'ip'; text: string } | null>(null)

  const members = customerAccounts().filter((a) => kindOf(a) === '일반회원')
  const known = new Set(members.map((m) => m.email))
  const pending = links.emails.filter((e) => !known.has(e))

  const addEmail = (e: FormEvent) => {
    e.preventDefault()
    if (!EMAIL.test(email.trim())) return setError({ at: 'email', text: '이메일 형식이 아닙니다.' })
    linkEmail(email)
    setEmail('')
    setError(null)
  }
  const addIp = (e: FormEvent) => {
    e.preventDefault()
    const r = rule.trim()
    if (!validRule(r) || !privateIp(r.split('/')[0])) return setError({ at: 'ip', text: '사내 IP(192.168.10.31)나 대역(192.168.10.0/24)만 넣을 수 있어요.' })
    linkIp(r)
    setRule('')
    setError(null)
  }
  const pcsOf = (r: string) => hosts.filter((h) => ipMatches(h.ip, r))

  return (
    <div className="space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-500">시스템 관리</p>
          <h1 className="text-2xl font-semibold">회원 연결</h1>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {company && <span className="chip bg-ink-100 text-ink-700">{company.name}</span>}
          <span className="chip bg-brand-50 text-brand-700">계정 {links.emails.length}</span>
          <span className="chip bg-brand-50 text-brand-700">IP {links.ips.length}</span>
        </div>
      </header>

      <section className="card space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">계정 연결</h2>
          <form onSubmit={addEmail} className="flex gap-2">
            <input className="field w-64" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="회원 이메일" aria-label="연결할 회원 이메일" />
            <button className="btn-primary px-4">연결</button>
          </form>
        </div>
        {error?.at === 'email' && <p className="text-sm text-red-600">{error.text}</p>}
        <div className="overflow-x-auto rounded-xl border border-ink-100">
          <table className="w-full text-sm">
            <thead className="bg-app text-left text-xs text-ink-500"><tr><th className="px-4 py-2.5 font-medium">이름</th><th className="px-4 py-2.5 font-medium">이메일</th><th className="px-4 py-2.5 font-medium">연결</th><th /></tr></thead>
            <tbody className="divide-y divide-ink-100">
              {members.map((m) => {
                const byEmail = links.emails.includes(m.email)
                return (
                  <tr key={m.id}>
                    <td className="px-4 py-2.5">{m.name}</td>
                    <td className="px-4 py-2.5 font-mono text-xs">{m.email}</td>
                    <td className="px-4 py-2.5">{byEmail ? <span className="chip bg-emerald-50 text-emerald-700">계정 연결</span> : <span className="chip bg-ink-100 text-ink-500">연결 전</span>}</td>
                    <td className="px-4 py-2.5 text-right">
                      {byEmail
                        ? <button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => unlinkEmail(m.email)}>해제</button>
                        : <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => linkEmail(m.email)}>연결</button>}
                    </td>
                  </tr>
                )
              })}
              {pending.map((e) => (
                <tr key={e}>
                  <td className="px-4 py-2.5 text-ink-500">가입 전</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{e}</td>
                  <td className="px-4 py-2.5"><span className="chip bg-emerald-50 text-emerald-700">계정 연결</span></td>
                  <td className="px-4 py-2.5 text-right"><button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => unlinkEmail(e)}>해제</button></td>
                </tr>
              ))}
              {!members.length && !pending.length && (
                <tr><td colSpan={4} className="px-4 py-6 text-center text-ink-500">가입한 일반회원이 없어요</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">부서·확인 권한</h2>
          <span className="chip bg-brand-50 text-brand-700">확인 권한 {customerAccounts().filter((a) => a.approver).length}</span>
        </div>
        <div className="overflow-x-auto rounded-xl border border-ink-100">
          <table className="w-full text-sm">
            <thead className="bg-app text-left text-xs text-ink-500"><tr><th className="px-4 py-2.5 font-medium">이름</th><th className="px-4 py-2.5 font-medium">구분</th><th className="px-4 py-2.5 font-medium">부서</th><th className="px-4 py-2.5 font-medium">확인 권한</th></tr></thead>
            <tbody className="divide-y divide-ink-100">
              {customerAccounts().map((m) => (
                <tr key={m.id}>
                  <td className="px-4 py-2.5">{m.name}<p className="font-mono text-xs text-ink-500">{m.email}</p></td>
                  <td className="px-4 py-2.5"><span className="chip bg-ink-100 text-ink-700">{kindOf(m)}</span></td>
                  <td className="px-4 py-2.5"><DeptPicker size="sm" label={`${m.name} 부서`} value={m.dept ?? ''} onChange={(d) => setProfile(m.email, { dept: d })} /></td>
                  <td className="px-4 py-2.5"><ApproverSwitch account={m} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">IP 연결</h2>
          <form onSubmit={addIp} className="flex gap-2">
            <input className="field w-64 font-mono" value={rule} onChange={(e) => setRule(e.target.value)} placeholder="192.168.10.31 또는 /24" aria-label="연결할 IP 또는 대역" />
            <button className="btn-primary px-4">연결</button>
          </form>
        </div>
        {error?.at === 'ip' && <p className="text-sm text-red-600">{error.text}</p>}
        {company && !links.ips.includes(company.network) && (
          <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => linkIp(company.network)}>사내망 전체 {company.network} 연결</button>
        )}
        <ul className="grid gap-2 md:grid-cols-2">
          {links.ips.map((r) => (
            <li key={r} className="flex items-center gap-3 rounded-xl border border-ink-100 bg-app px-4 py-2.5 text-sm">
              <span className="font-mono">{r}</span>
              <span className="text-xs text-ink-500">사내 PC {pcsOf(r).length}대</span>
              <button type="button" className="btn-quiet ml-auto px-2 py-1 text-xs" onClick={() => unlinkIp(r)}>해제</button>
            </li>
          ))}
          {!links.ips.length && <li className="text-sm text-ink-500">연결한 IP가 없어요</li>}
        </ul>
      </section>
    </div>
  )
}
