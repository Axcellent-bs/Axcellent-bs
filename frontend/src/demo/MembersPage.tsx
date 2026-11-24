import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { useCompany } from './company'
import { linkEmail, setProfile, unlinkEmail, useLinks, useProfiles } from './members'
import { DeptPicker } from './DeptPicker'
import { customerAccounts, kindOf, useSession, type Account } from './session'
import { reloadServerMembers, useServerMembers } from './serverMembers'
import { createAccount, unlinkMember, updateAccount } from '@/lib/authApi'
import { accountInputProblem, getSession, MIN_PASSWORD_LENGTH, type ServerAccount, type ServerRole } from '@/lib/humanSession'
import { approverOf, canEditProfile, customerRows, roleChangeProblem, type AccountPatch } from '@/lib/memberAdmin'
import { More, PageHead, SectionHead, StatusChip, Tabs, Tiles } from '@/components/connect/parts'
import { DejafixCard } from '@/components/connect/DejafixConnect'
import { ConnectedPcs } from '@/components/connect/ConnectedPcs'

// 회원·서버 관리 (관리자 /system/members). 회사 사람과 PC를 한 화면 탭으로 관리한다.
// - 회원: 계정 연결(이메일)·부서(선택, 표시·조직도용)·확인 권한·열람 담당(인사·재무 AI 대화 요약)·쓰는 PC를 한 표에서
// - DejaFix 연결(탭 id pcs 유지): 장애 대응이 쓰는 DejaFix 연결 상태 + 연결한 PC(DejaFix 장애 목록에 나온 호스트의 agent 응답 여부).
//   예전 /system/hosts 주소는 이 탭으로 온다. 예전 "PC·서버" 목록(InfraHosts)은 코드에 박힌 예시 PC와 가짜 연결 확인이라 뺐다(11/19)
// - 접속 허용: 사내 PC IP나 대역. 그 PC에서 가입·로그인하면 회사 회원이 된다
// 계정 연결과 접속 허용 중 하나라도 맞으면 그 회원은 회사 회원이다.
// 서버 로그인이 켜져 있으면 회원 표는 서버 계정 목록(dashboard-api `GET /auth/accounts`)만 쓴다. 관리자는 일반회원을
// 만들고(`POST /auth/accounts`), 역할·부서·확인 권한·열람 담당을 서버에 저장한다(`POST /auth/accounts/{id}/update`).
// 브라우저에 기억한 계정·프로필은 보지 않는다. 서버에 저장소가 없는 연결(이메일·PC IP)·쓰는 PC 칸과 접속 허용 탭은 "준비 중"이다.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const TABS = [
  { id: 'members', label: '회원' },
  { id: 'pcs', label: 'DejaFix 연결' },
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
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.some((t) => t.id === params.get('tab')) ? (params.get('tab') as Tab) : 'members'
  const { account, mode } = useSession()
  const serverMode = mode === 'server'
  // 서버 로그인: 서버 계정 목록을 받으면 다시 그린다(customerAccounts가 이 목록을 쓴다)
  useServerMembers(serverMode && (!!account?.admin || !!account?.operator))
  const accounts = customerAccounts()

  const approvers = accounts.filter((a) => a.approver).length
  const counts: Record<Tab, number | undefined> = { members: accounts.length, pcs: undefined }

  return (
    <div className="space-y-8 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="회원·서버 관리">
        {company?.name ?? '회사'}의 회원과 PC를 관리해요. 관리자가 만든(연결한) 회원만 이 회사를 쓸 수 있어요.
      </PageHead>

      <Tiles label="회원·서버 요약" items={[
        { name: '회원', value: `${accounts.length}명`, tone: 'ok', note: serverMode ? '서버 계정' : `계정 연결 ${links.emails.length}개` },
        { name: '확인 권한', value: `${approvers}명`, tone: approvers ? 'ok' : 'wait', note: approvers ? 'AI 제안을 승인할 수 있어요' : '승인할 사람이 없어요' },
      ]} />

      <Tabs label="회원·서버 관리" value={tab} items={TABS.map((t) => ({ ...t, count: counts[t.id] }))}
        onPick={(id) => setParams(id === 'members' ? {} : { tab: id }, { replace: true })} />

      {tab === 'members' && <Members />}
      {tab === 'pcs' && (
        <div className="space-y-8">
          <DejafixCard admin={!!account?.admin} />
          <ConnectedPcs />
        </div>
      )}
    </div>
  )
}

/** 회원 한 표: 연결 · 부서 · 확인 권한 · 열람 담당 · 쓰는 PC */
/** 서버 로그인: 일반회원 계정 만들기 (비밀번호는 서버로만 보내고 화면에 남기지 않는다) */
function CreateMember({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setDone(null)
    const problem = accountInputProblem(form)
    if (problem) return setError(problem)
    setBusy(true)
    try {
      const row = await createAccount({ ...form, role: 'member' })
      setForm({ name: '', email: '', password: '' })
      setError(null)
      setDone(`${row.name}(${row.email}) 계정을 만들었어요. 비밀번호는 본인에게 따로 알려 주세요.`)
      onCreated()
    } catch (x) {
      setError(x instanceof Error ? x.message : '계정을 만들지 못했어요.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={(e) => void submit(e)} className="card space-y-3 p-5">
      <h3 className="card-title">일반회원 계정 만들기</h3>
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_1fr_auto]">
        <input className="field" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="이름" aria-label="회원 이름" />
        <input className="field" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="회원 이메일" aria-label="회원 이메일" autoComplete="off" />
        <input className="field" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder={`처음 비밀번호 (${MIN_PASSWORD_LENGTH}자 이상)`} aria-label="처음 비밀번호" autoComplete="new-password" />
        <button className="btn-primary px-4" disabled={busy}>{busy ? '만드는 중…' : '계정 만들기'}</button>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {done && <p className="text-sm text-emerald-700">{done}</p>}
      <p className="text-xs text-ink-500">새 계정은 일반회원으로 만들어요. 관리자로 바꾸려면 아래 표의 구분 칸에서 골라 주세요.</p>
    </form>
  )
}

function Members() {
  const { mode } = useSession()
  return mode === 'server' ? <ServerMembers /> : <DemoMembers />
}

const ROLE_LABEL: Record<ServerRole, string> = { operator: '운영자', admin: '관리자', member: '일반회원' }
const CHOOSABLE: ServerRole[] = ['admin', 'member']

/** 서버 로그인: 부서 고르기. 고른 값은 "저장"을 눌러야 서버에 저장한다(직접 입력 중 글자마다 보내지 않게) */
function ServerDeptCell({ row, disabled, onSave }: { row: ServerAccount; disabled: boolean; onSave: (dept: string) => void }) {
  const saved = row.dept ?? ''
  const [draft, setDraft] = useState(saved)
  return (
    <div className="flex items-center gap-2">
      <DeptPicker size="sm" label={`${row.name} 부서`} value={draft} onChange={setDraft} allowNone />
      {draft.trim() !== saved && (
        <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={disabled} onClick={() => onSave(draft)}>저장</button>
      )}
    </div>
  )
}

/**
 * 서버 로그인 회원 표: 서버 계정 목록만 쓴다. 바꾸면 서버에 저장하고, 성공하면 목록을 다시 읽는다.
 * 연결(이메일·PC IP)·쓰는 PC는 서버에 저장소가 없어 "준비 중"이다.
 */
function ServerMembers() {
  const { refreshMe } = useSession()
  const me = getSession()?.account ?? null
  const { rows, error, loading } = useServerMembers(true)
  const list = customerRows(rows ?? [])
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const save = async (target: ServerAccount, patch: AccountPatch, done: string) => {
    setBusy(target.id)
    setMsg(null)
    try {
      await updateAccount(target.id, patch)
      setMsg({ ok: true, text: done })
    } catch (x) {
      setMsg({ ok: false, text: x instanceof Error ? x.message : '저장하지 못했어요.' })
    } finally {
      // 실패해도 다시 읽어 표를 서버 값으로 맞춘다
      await reloadServerMembers()
      if (me && target.id === me.id) await refreshMe()
      setBusy(null)
    }
  }
  const unlink = async (target: ServerAccount) => {
    setBusy(target.id)
    setMsg(null)
    try {
      await unlinkMember(target.id)
      setMsg({ ok: true, text: `${target.name} 연결을 끊었어요. 그 계정은 연결 대기가 돼요.` })
    } catch (x) {
      setMsg({ ok: false, text: x instanceof Error ? x.message : '연결을 끊지 못했어요.' })
    } finally {
      await reloadServerMembers()
      setBusy(null)
    }
  }
  const changeRole = (target: ServerAccount, next: ServerRole) => {
    if (!me || next === target.role) return
    const problem = roleChangeProblem(me, target, next, list)
    if (problem) return setMsg({ ok: false, text: problem })
    void save(target, { role: next }, `${target.name} 계정을 ${ROLE_LABEL[next]}(으)로 바꿨어요. 그 계정은 다시 로그인해야 해요.`)
  }

  return (
    <section className="space-y-4">
      <SectionHead title="회원" desc="서버에 있는 회사 계정이에요. 바꾼 값은 서버에 저장돼 다른 PC에서도 같게 보여요."
        right={<button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={loading} onClick={() => void reloadServerMembers()}>{loading ? '읽는 중…' : '다시 읽기'}</button>} />
      <CreateMember onCreated={() => void reloadServerMembers()} />
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {msg && <p role="status" className={`text-sm ${msg.ok ? 'text-emerald-700' : 'text-red-700'}`}>{msg.text}</p>}
      <div className="card overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">이름</th>
              <th className="px-4 py-2.5 font-medium">구분</th>
              <th className="px-4 py-2.5 font-medium">부서 (선택)</th>
              <th className="px-4 py-2.5 font-medium">확인 권한</th>
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {list.map((a) => {
              const editable = !!me && canEditProfile(me, a) && busy === null
              const approver = approverOf(a)
              // 바꿀 수 없는 역할이면 이유를 title로 보여 준다
              const other = CHOOSABLE.find((r) => r !== a.role)
              const roleProblem = !me ? '로그인이 필요해요.' : other ? roleChangeProblem(me, a, other, list) : null
              return (
                <tr key={a.id}>
                  <td className="px-4 py-2.5">
                    <p className="font-medium text-ink-900">{a.name}{me?.id === a.id && <span className="chip ml-2 bg-ink-100 text-ink-700">나</span>}</p>
                    <p className="font-mono text-xs text-ink-500">{a.email}</p>
                  </td>
                  <td className="px-4 py-2.5">
                    <select className="field w-auto py-1.5 text-xs" aria-label={`${a.name} 구분`} value={a.role}
                      disabled={!editable || !!roleProblem} title={roleProblem ?? undefined}
                      onChange={(e) => changeRole(a, e.target.value as ServerRole)}>
                      {CHOOSABLE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                    </select>
                  </td>
                  <td className="px-4 py-2.5">
                    <ServerDeptCell key={`${a.id}:${a.dept ?? ''}`} row={a} disabled={!editable}
                      onSave={(dept) => void save(a, { dept }, dept.trim() ? `${a.name} 부서를 ${dept.trim()}(으)로 저장했어요.` : `${a.name} 부서를 비웠어요.`)} />
                  </td>
                  <td className="px-4 py-2.5">
                    <button type="button" role="switch" aria-checked={approver} aria-label={`${a.name} 확인 권한`} disabled={!editable}
                      onClick={() => void save(a, { approver: !approver }, `${a.name} 확인 권한을 ${approver ? '껐어요' : '켰어요'}.`)}
                      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${approver ? 'bg-brand-600' : 'bg-ink-100'}`}>
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${approver ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {a.role === 'member' && editable && (!a.linked_admin || a.linked_admin === me?.id || me?.role === 'operator') && (
                      <button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => void unlink(a)}>연결 끊기</button>
                    )}
                  </td>
                </tr>
              )
            })}
            {!list.length && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-ink-500">{rows === null ? (loading ? '계정 목록을 읽는 중이에요' : '계정 목록을 아직 받지 못했어요') : '계정이 없어요'}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <More summary="칸마다 무슨 뜻인가요">
        <p>구분을 바꾸면 그 계정의 로그인이 끊겨 다시 로그인해야 해요. 자기 구분은 스스로 못 바꾸고, 마지막 남은 관리자는 내릴 수 없어요.</p>
        <p>부서는 고르지 않아도 되고, 표시와 조직도에만 써요.</p>
        <p>확인 권한이 있으면 AI가 제안한 일을 승인할 수 있어요.</p>
        <p>연결 끊기를 누르면 그 일반회원은 이 회사에서 빠져 연결 대기가 돼요(계정은 남아요). 내가 연결한 회원만 끊을 수 있어요.</p>
      </More>
    </section>
  )
}

/** 데모 로그인 회원 표: 브라우저에 기억한 계정·연결·프로필(members.ts) */
function DemoMembers() {
  const links = useLinks()
  useProfiles() // 부서·확인 권한을 바꾸면 다시 그린다
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)

  const accounts = customerAccounts()
  const known = new Set(accounts.map((m) => m.email))
  const pending = links.emails.filter((e) => !known.has(e))

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
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {accounts.map((m) => {
              const member = kindOf(m) === '일반회원'
              const byEmail = links.emails.includes(m.email)
              return (
                <tr key={m.id}>
                  <td className="px-4 py-2.5"><p className="font-medium text-ink-900">{m.name}</p><p className="font-mono text-xs text-ink-500">{m.email}</p></td>
                  <td className="px-4 py-2.5 text-ink-700">{kindOf(m)}</td>
                  <td className="px-4 py-2.5">
                    {!member ? <span className="text-xs text-ink-500">–</span>
                      : m.server ? <StatusChip tone="ok">서버 계정</StatusChip>
                      : byEmail ? <StatusChip tone="ok">계정 연결</StatusChip>
                      : <StatusChip tone="off">연결 전</StatusChip>}
                  </td>
                  <td className="px-4 py-2.5"><DeptPicker size="sm" label={`${m.name} 부서`} value={m.dept ?? ''} onChange={(d) => setProfile(m.email, { dept: d })} allowNone /></td>
                  <td className="px-4 py-2.5"><ApproverSwitch account={m} /></td>
                  <td className="px-4 py-2.5 text-right">
                    {member && !m.server && (byEmail
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
