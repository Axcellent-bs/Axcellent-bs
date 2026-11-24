import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useSession } from '@/demo/session'
import { createAccount, listAccounts, resetPassword } from '@/lib/authApi'
import { accountInputProblem, canResetPassword, getSession, MIN_PASSWORD_LENGTH, type ServerAccount, type ServerRole } from '@/lib/humanSession'
import { PreparingChip } from '@/components/Preparing'

// 계정 (AXcellent 운영팀, /control/accounts). 여기서는 운영자 계정만 만든다(팀원마다 하나씩, 설계 "정한 것").
// 고객사 관리자는 우리가 만들지 않는다: 고객사가 /setup에서 우리가 발급한 회사 전용 키로 직접 가입하고(비밀번호도
// 고객사가 정한다), 일반회원은 그 관리자가 만든다(노예진 11/24). 고객사 계정 비밀번호도 여기서 초기화하지 않는다.
// 서버 로그인이 꺼져 있으면(데모 로그인) 서버 계정이 없어 이 화면은 "준비 중"이다.
const ROLE_LABEL: Record<ServerRole, string> = { operator: '운영자', admin: '고객사 관리자', member: '일반회원' }
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—')

export default function Accounts() {
  const { mode, account } = useSession()
  const serverOperator = mode === 'server' && !!account?.server && !!account.operator
  const [rows, setRows] = useState<ServerAccount[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [form, setForm] = useState<{ name: string; email: string; role: ServerRole; password: string }>({ name: '', email: '', role: 'operator', password: '' })
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 비밀번호 초기화: 고른 계정 하나만 입력칸을 연다. 임시 비밀번호는 입력칸 상태에만 둔다
  const [resetFor, setResetFor] = useState<string | null>(null)
  const [resetPw, setResetPw] = useState('')
  const [resetMsg, setResetMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const me = getSession()?.account

  const startReset = (id: string | null) => {
    setResetFor(id)
    setResetPw('')
    setResetMsg(null)
  }
  const submitReset = async (row: ServerAccount) => {
    if (resetPw.length < MIN_PASSWORD_LENGTH) return setResetMsg({ ok: false, text: `임시 비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 해요.` })
    setBusy(true)
    try {
      await resetPassword(row.id, resetPw)
      setResetFor(null)
      setResetPw('')
      setResetMsg({ ok: true, text: `${row.name}(${row.email}) 비밀번호를 초기화했어요. 그 계정의 로그인은 모두 끊겼어요. 임시 비밀번호는 본인에게 따로 알려 주세요.` })
    } catch (x) {
      setResetMsg({ ok: false, text: x instanceof Error ? x.message : '비밀번호를 초기화하지 못했어요.' })
    } finally {
      setBusy(false)
    }
  }

  const reload = useCallback(() => {
    listAccounts().then((r) => { setRows(r); setLoadError(null) }).catch((x: Error) => setLoadError(x.message))
  }, [])
  useEffect(() => {
    if (serverOperator) reload()
  }, [serverOperator, reload])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setDone(null)
    const problem = accountInputProblem(form)
    if (problem) return setError(problem)
    setBusy(true)
    try {
      const row = await createAccount(form)
      setForm({ name: '', email: '', role: form.role, password: '' })
      setError(null)
      setDone(`${row.name}(${row.email}, ${ROLE_LABEL[row.role]}) 계정을 만들었어요. 비밀번호는 팀 채널이나 본인에게 따로 알려 주세요.`)
      reload()
    } catch (x) {
      setError(x instanceof Error ? x.message : '계정을 만들지 못했어요.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 px-8 pb-12">
      <header>
        <p className="text-sm text-ink-500">관제 센터</p>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">계정 {!serverOperator && <PreparingChip />}</h1>
        <p className="mt-1 text-sm text-ink-500">AXcellent 운영자 계정을 만들어요. 팀원마다 하나씩 만들어 기록에 실제 이름이 남게 해요. 고객사 관리자는 고객사가 발급 키로 직접 가입해요(연결 키 발급에서 회사 이름을 넣어 발급).</p>
      </header>

      {!serverOperator ? (
        <p role="status" className="rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
          {mode === 'server'
            ? '서버 운영자 계정으로 로그인하면 계정을 만들고 볼 수 있어요.'
            : '서버 로그인이 아직 꺼져 있어 데모 로그인으로 들어왔어요. 서버 로그인이 켜지면 여기서 계정을 만들어요.'}
        </p>
      ) : (
        <>
          <form onSubmit={(e) => void submit(e)} className="card space-y-3 p-6">
            <h2 className="card-title">새 운영자 계정</h2>
            <div className="grid gap-2 md:grid-cols-[1fr_1fr_1fr_auto]">
              <input className="field" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="이름" aria-label="이름" />
              <input className="field" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="이메일" aria-label="이메일" autoComplete="off" />
              <input className="field" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder={`처음 비밀번호 (${MIN_PASSWORD_LENGTH}자 이상)`} aria-label="처음 비밀번호" autoComplete="new-password" />
              <button className="btn-primary px-5" disabled={busy}>{busy ? '만드는 중…' : '만들기'}</button>
            </div>
            {error && <p className="text-sm text-red-700">{error}</p>}
            {done && <p className="text-sm text-emerald-700">{done}</p>}
          </form>

          <section className="card overflow-x-auto">
            <table className="w-full whitespace-nowrap text-sm">
              <thead className="bg-app text-left text-xs text-ink-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">이름</th>
                  <th className="px-4 py-2.5 font-medium">역할</th>
                  <th className="px-4 py-2.5 font-medium">만든 시각</th>
                  <th className="px-4 py-2.5 font-medium">비밀번호</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {(rows ?? []).map((r) => (
                  <tr key={r.id}>
                    <td className="px-4 py-2.5"><p className="font-medium text-ink-900">{r.name}</p><p className="font-mono text-xs text-ink-500">{r.email}</p></td>
                    <td className="px-4 py-2.5"><span className="chip bg-ink-100 text-ink-700">{ROLE_LABEL[r.role] ?? r.role}</span></td>
                    <td className="px-4 py-2.5 text-ink-500">{day(r.created_at)}</td>
                    <td className="px-4 py-2.5">
                      {!me || r.role !== 'operator' || !canResetPassword(me, r) ? (
                        <span className="text-xs text-ink-500">{me?.id === r.id ? '내 계정은 오른쪽 위 메뉴에서 바꿔요' : r.role !== 'operator' ? '고객사가 직접 관리해요' : '—'}</span>
                      ) : resetFor === r.id ? (
                        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void submitReset(r) }}>
                          <input className="field w-56" type="password" value={resetPw} onChange={(e) => setResetPw(e.target.value)}
                            placeholder={`임시 비밀번호 (${MIN_PASSWORD_LENGTH}자 이상)`} aria-label={`${r.name} 임시 비밀번호`} autoComplete="new-password" />
                          <button className="btn-primary px-4" disabled={busy}>{busy ? '초기화 중…' : '초기화'}</button>
                          <button type="button" className="btn-ghost px-3" onClick={() => startReset(null)}>취소</button>
                        </form>
                      ) : (
                        <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => startReset(r.id)}>비밀번호 초기화</button>
                      )}
                    </td>
                  </tr>
                ))}
                {rows && !rows.length && <tr><td colSpan={4} className="px-4 py-6 text-center text-ink-500">계정이 없어요</td></tr>}
                {!rows && !loadError && <tr><td colSpan={4} className="px-4 py-6 text-center text-ink-500">불러오는 중…</td></tr>}
              </tbody>
            </table>
            {resetMsg && <p role="status" className={`px-4 py-3 text-sm ${resetMsg.ok ? 'text-emerald-700' : 'text-red-700'}`}>{resetMsg.text}</p>}
            {loadError && <p className="px-4 py-3 text-sm text-red-700">{loadError}</p>}
          </section>
        </>
      )}
    </div>
  )
}
