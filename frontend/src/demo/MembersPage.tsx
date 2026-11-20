import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { useCompany } from './company'
import { READER_AREAS, ipMatches, linkEmail, linkIp, setProfile, unlinkEmail, unlinkIp, useLinks, useProfiles, validRule } from './members'
import { DeptPicker } from './DeptPicker'
import { customerAccounts, kindOf, useSession, type Account } from './session'
import { reloadServerMembers, useServerMembers } from './serverMembers'
import { createAccount, updateAccount } from '@/lib/authApi'
import { accountInputProblem, getSession, MIN_PASSWORD_LENGTH, type ServerAccount, type ServerRole } from '@/lib/humanSession'
import { parseTenants, tenantListProblem } from '@/lib/secretStore'
import { approverOf, buyerIdProblem, canEditProfile, customerRows, limitEditorOf, limitEditorProblem, policyEditorOf, policyEditorProblem, roleChangeProblem, secretEditorOf, secretEditorProblem, secretTenantsOf, toggleReader, type AccountPatch } from '@/lib/memberAdmin'
import { PreparingChip } from '@/components/Preparing'
import { privateIp, useHosts } from './store'
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
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.some((t) => t.id === params.get('tab')) ? (params.get('tab') as Tab) : 'members'
  const { account, mode } = useSession()
  const serverMode = mode === 'server'
  // 서버 로그인: 서버 계정 목록을 받으면 다시 그린다(customerAccounts가 이 목록을 쓴다)
  useServerMembers(serverMode && (!!account?.admin || !!account?.operator))
  const accounts = customerAccounts()

  const approvers = accounts.filter((a) => a.approver).length
  const counts: Record<Tab, number | undefined> = { members: accounts.length, pcs: undefined, access: serverMode ? undefined : links.ips.length }

  return (
    <div className="space-y-8 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="회원·서버 관리">
        {company?.name ?? '회사'}의 회원과 PC를 관리해요. 이메일을 연결하거나 허용한 사내 IP에서 접속하면 회사 회원이 돼요.
      </PageHead>

      <Tiles label="회원·서버 요약" items={[
        { name: '회원', value: `${accounts.length}명`, tone: 'ok', note: serverMode ? '서버 계정' : `계정 연결 ${links.emails.length}개` },
        { name: '확인 권한', value: `${approvers}명`, tone: approvers ? 'ok' : 'wait', note: approvers ? 'AI 제안을 승인할 수 있어요' : '승인할 사람이 없어요' },
        serverMode
          ? { name: '접속 허용', value: <PreparingChip />, tone: 'off', note: '사내 IP·대역' }
          : { name: '접속 허용', value: `${links.ips.length}개`, tone: links.ips.length ? 'ok' : 'off', note: '사내 IP·대역' },
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
      {tab === 'access' && <Access />}
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

/** 구매자 연결(shop-api 구매자 번호). 비우고 저장하면 연결을 푼다. 바뀌면 저장 버튼이 나온다 */
function BuyerCell({ row, disabled, reason, onSave }: { row: ServerAccount; disabled: boolean; reason?: string; onSave: (buyerId: string) => void }) {
  const saved = row.buyer_id ?? ''
  const [draft, setDraft] = useState(saved)
  const problem = buyerIdProblem(draft)
  return (
    <div className="flex items-center gap-1">
      <input className="field w-28 py-1 font-mono text-xs" aria-label={`${row.name} 구매자 번호`} placeholder="buy_0001" value={draft} disabled={disabled}
        title={reason} onChange={(e) => setDraft(e.target.value)} />
      {draft.trim() !== saved && (
        <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={disabled || !!problem} title={problem ?? undefined} onClick={() => onSave(draft)}>저장</button>
      )}
    </div>
  )
}

/** 키 담당 고객사 목록 입력(쉼표로 구분). 바뀌면 저장 버튼이 나온다 */
function SecretTenantsCell({ row, disabled, onSave }: { row: ServerAccount; disabled: boolean; onSave: (tenants: string[]) => void }) {
  const saved = secretTenantsOf(row).join(', ')
  const [draft, setDraft] = useState(saved)
  const problem = tenantListProblem(draft)
  return (
    <div className="flex items-center gap-1">
      <input className="field w-40 py-1 text-xs" aria-label={`${row.name} 담당 고객사`} placeholder="고객사 ID, 쉼표로 구분" value={draft} disabled={disabled}
        onChange={(e) => setDraft(e.target.value)} />
      {draft.trim() !== saved && (
        <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={disabled || !!problem} title={problem ?? undefined} onClick={() => onSave(parseTenants(draft))}>저장</button>
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
              <th className="px-4 py-2.5 font-medium">연결</th>
              <th className="px-4 py-2.5 font-medium">부서 (선택)</th>
              <th className="px-4 py-2.5 font-medium">구매자 번호</th>
              <th className="px-4 py-2.5 font-medium">확인 권한</th>
              <th className="px-4 py-2.5 font-medium">비용 상한 변경 권한</th>
              <th className="px-4 py-2.5 font-medium">정책 변경 권한</th>
              <th className="px-4 py-2.5 font-medium">키 담당 (고객사)</th>
              <th className="px-4 py-2.5 font-medium">열람 담당</th>
              <th className="px-4 py-2.5 font-medium">쓰는 PC</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {list.map((a) => {
              const editable = !!me && canEditProfile(me, a) && busy === null
              const approver = approverOf(a)
              const selfBuyerBlocked = !!me && me.id === a.id && me.role !== 'operator' // 서버도 막는다(SELF_BUYER_LINK)
              const readers = a.readers ?? []
              const limitEditor = limitEditorOf(a)
              const limitProblem = limitEditorProblem(a)
              const policyEditor = policyEditorOf(a)
              const secretEditor = secretEditorOf(a)
              const secretProblem = me ? secretEditorProblem(me, a) : '로그인이 필요해요.'
              const policyProblem = me ? policyEditorProblem({ role: me.role, policy_editor: me.policy_editor }, a) : '로그인이 필요해요.'
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
                  <td className="px-4 py-2.5"><PreparingChip /></td>
                  <td className="px-4 py-2.5">
                    <ServerDeptCell key={`${a.id}:${a.dept ?? ''}`} row={a} disabled={!editable}
                      onSave={(dept) => void save(a, { dept }, dept.trim() ? `${a.name} 부서를 ${dept.trim()}(으)로 저장했어요.` : `${a.name} 부서를 비웠어요.`)} />
                  </td>
                  <td className="px-4 py-2.5">
                    <BuyerCell key={`${a.id}:${a.buyer_id ?? ''}`} row={a} disabled={!editable || selfBuyerBlocked}
                      reason={selfBuyerBlocked ? '자기 계정의 구매자 연결은 운영자만 바꿀 수 있어요.' : undefined}
                      onSave={(buyerId) => void save(a, { buyer_id: buyerId }, buyerId.trim() ? `${a.name} 구매자를 ${buyerId.trim()}(으)로 연결했어요.` : `${a.name} 구매자 연결을 풀었어요.`)} />
                  </td>
                  <td className="px-4 py-2.5">
                    <button type="button" role="switch" aria-checked={approver} aria-label={`${a.name} 확인 권한`} disabled={!editable}
                      onClick={() => void save(a, { approver: !approver }, `${a.name} 확인 권한을 ${approver ? '껐어요' : '켰어요'}.`)}
                      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${approver ? 'bg-brand-600' : 'bg-ink-100'}`}>
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${approver ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-2.5">
                    <button type="button" role="switch" aria-checked={limitEditor} aria-label={`${a.name} 비용 상한 변경 권한`}
                      disabled={!editable || (!!limitProblem && !limitEditor)} title={limitProblem ?? undefined}
                      onClick={() => void save(a, { limit_editor: !limitEditor }, `${a.name} 비용 상한 변경 권한을 ${limitEditor ? '껐어요' : '켰어요'}.`)}
                      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${limitEditor ? 'bg-brand-600' : 'bg-ink-100'}`}>
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${limitEditor ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-2.5">
                    <button type="button" role="switch" aria-checked={policyEditor} aria-label={`${a.name} 정책 변경 권한`}
                      disabled={!editable || (!!policyProblem && !policyEditor)} title={policyProblem ?? undefined}
                      onClick={() => void save(a, { policy_editor: !policyEditor }, `${a.name} 정책 변경 권한을 ${policyEditor ? '껐어요' : '켰어요'}.`)}
                      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${policyEditor ? 'bg-brand-600' : 'bg-ink-100'}`}>
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${policyEditor ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <button type="button" role="switch" aria-checked={secretEditor} aria-label={`${a.name} 키 담당 권한`}
                        disabled={!editable || (!!secretProblem && !secretEditor)} title={secretProblem ?? undefined}
                        onClick={() => void save(a, { secret_editor: !secretEditor }, `${a.name} 키 담당 권한을 ${secretEditor ? '껐어요' : '켰어요'}.`)}
                        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${secretEditor ? 'bg-brand-600' : 'bg-ink-100'}`}>
                        <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${secretEditor ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </button>
                      {secretEditor && (
                        <SecretTenantsCell key={`${a.id}:${secretTenantsOf(a).join(',')}`} row={a} disabled={!editable || !!secretProblem}
                          onSave={(tenants) => void save(a, { secret_tenants: tenants }, `${a.name} 담당 고객사를 저장했어요.`)} />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {a.role === 'admin' ? <span className="text-xs text-ink-500">관리자는 모두 봐요</span> : (
                      <div className="flex gap-1" role="group" aria-label={`${a.name} 열람 담당`}>
                        {READER_AREAS.map((r) => {
                          const sel = readers.includes(r.id)
                          return (
                            <button key={r.id} type="button" aria-pressed={sel} disabled={!editable}
                              onClick={() => void save(a, { readers: toggleReader(readers, r.id) }, `${a.name} 열람 담당을 저장했어요.`)}
                              className={`chip disabled:opacity-50 ${sel ? 'bg-brand-600 text-white' : 'bg-ink-50 text-ink-500 hover:bg-ink-100'}`}>{r.label}</button>
                          )
                        })}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5"><PreparingChip /></td>
                </tr>
              )
            })}
            {!list.length && (
              <tr><td colSpan={10} className="px-4 py-6 text-center text-ink-500">{rows === null ? (loading ? '계정 목록을 읽는 중이에요' : '계정 목록을 아직 받지 못했어요') : '계정이 없어요'}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <More summary="칸마다 무슨 뜻인가요">
        <p>구분을 바꾸면 그 계정의 로그인이 끊겨 다시 로그인해야 해요. 자기 구분은 스스로 못 바꾸고, 마지막 남은 관리자는 내릴 수 없어요.</p>
        <p>부서는 고르지 않아도 되고, 표시와 조직도에만 써요.</p>
        <p>확인 권한이 있으면 AI가 제안한 일을 승인할 수 있어요.</p>
        <p>AI 대화방의 다른 사람 대화 요약은 모두가 봐요. 인사·재무 AI만 관리자와 열람 담당으로 고른 사람이 봐요.</p>
        <p>연결(이메일·PC IP)과 쓰는 PC는 서버에 아직 저장하지 않아 준비 중이에요.</p>
      </More>
    </section>
  )
}

/** 데모 로그인 회원 표: 브라우저에 기억한 계정·연결·프로필(members.ts) */
function DemoMembers() {
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
                      : m.server ? <StatusChip tone="ok">서버 계정</StatusChip>
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

/** 접속 허용: 사내 PC IP·대역 */
function Access() {
  const { mode } = useSession()
  if (mode === 'server') {
    return (
      <section className="space-y-4">
        <SectionHead title="접속 허용" desc="사내 PC IP로 회원을 잇는 기능이에요." right={<PreparingChip />} />
        <p role="status" className="rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
          서버 로그인에서는 아직 PC IP로 회원을 잇지 않아요. 회원은 회원 탭에서 계정을 만들어 주세요.
        </p>
      </section>
    )
  }
  return <DemoAccess />
}

/** 데모 로그인: 이 브라우저에 IP 규칙을 기억한다(members.ts) */
function DemoAccess() {
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
