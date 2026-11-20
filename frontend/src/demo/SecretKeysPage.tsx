import { useCallback, useState } from 'react'
import { Link } from 'react-router'
import { PageHead } from '@/components/connect/parts'
import { approvalLink } from '@/lib/briefMedia'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { shortDateTime } from '@/lib/format'
import { useVisibleInterval } from '@/lib/visibleInterval'
import {
  canTryApply, hintKo, historyLineKo, nameProblem, openRequests, readSecretState, requestLineKo, secretHttpErrorKo, valueProblem,
  writeResultKo, type SecretState,
} from '@/lib/secretStore'
import { addSecret, applySecretRequest, deleteSecret, getSecrets, replaceSecret } from '@/lib/secretStoreApi'

// 키 관리 (관리자 /system/keys). 고객사별 API 키·토큰을 암호화 DB에 넣고 바꾼다.
// - 값은 한 번 넣으면 다시 볼 수 없다(조회 기능이 없다). 화면에는 '설정됨'과 끝 4자리 이하만 보인다.
// - 값 입력칸은 type=password이고, 보낸 즉시 비운다. 값은 화면 상태·로그·주소에 남기지 않는다.
// - 추가는 키 담당자가 바로. 교체·삭제는 요청자와 다른 담당자의 승인 뒤 '승인됨 → 적용'을 눌러야 바뀐다.
// - 키 담당 권한과 담당 고객사는 운영자가 회원·서버 관리에서 정한다. 권한이 없으면 읽기 전용 안내만 보인다.
const REFRESH_MS = 20_000
const TONE = { ok: 'bg-brand-50 text-brand-700', warn: 'bg-amber-50 text-amber-800', bad: 'bg-red-50 text-red-800' } as const

type Shown = { kind: 'done' | 'held' | 'failed'; text: string; reviewId?: string | null }
type Op = 'add' | 'replace' | 'delete'

export default function SecretKeysPage() {
  const [state, setState] = useState<SecretState | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tenant, setTenant] = useState('')
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState<Shown | null>(null)

  const load = useCallback(async () => {
    try {
      const next = readSecretState(await getSecrets())
      setState(next)
      setError(next ? null : '응답을 읽지 못했어요.')
    } catch (e) {
      setState(null)
      setError(secretHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code))
    } finally {
      setLoaded(true)
    }
  }, [])
  useVisibleInterval(load, REFRESH_MS)

  const canEdit = !!state?.canEdit && HUMAN_TOKEN_SET
  const activeTenant = tenant || state?.myTenants[0] || ''
  const nameIssue = name ? nameProblem(name) : null
  const valueIssue = value ? valueProblem(value) : null
  const open = state ? openRequests(state) : []

  async function run(action: () => Promise<Shown>, after?: () => void) {
    if (busy) return // 클릭 잠금
    setBusy(true)
    setShown(null)
    try {
      const out = await action()
      setShown(out)
      if (out.kind !== 'failed') after?.()
    } catch (e) {
      setShown({ kind: 'failed', text: secretHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) })
    } finally {
      setBusy(false)
      void load()
    }
  }

  // 값은 지역 변수로 한 번 꺼내 보내고, 보내기 전에 입력칸을 비운다
  function submit(op: Op) {
    const sent = value
    setValue('')
    void run(async () => {
      const out = op === 'add' ? await addSecret(activeTenant, name, sent)
        : op === 'replace' ? await replaceSecret(activeTenant, name, sent, reason.trim())
          : await deleteSecret(activeTenant, name, reason.trim())
      return writeResultKo(out)
    }, () => { setName(''); setReason('') })
  }

  const baseOk = canEdit && !busy && !!activeTenant && !!name && !nameIssue
  const noValue = (op: Op) => (op === 'delete' ? true : !!value && !valueIssue)

  return (
    <div className="space-y-6 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="키 관리">
        고객사별 API 키·토큰을 암호화해 저장해요. 한 번 넣은 값은 다시 볼 수 없고 교체만 할 수 있어요. 화면에는 '설정됨'과 끝 4자리 이하만 보여요.
      </PageHead>

      {!loaded && <p className="text-sm text-ink-500">불러오는 중</p>}
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}

      {state && !state.enabled && (
        <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>키 저장소가 꺼져 있어요(서버 설정 AX_SECRET_STORE=env). 지금은 서버 .env의 키를 그대로 써요. 서버 담당자가 저장소를 켜야 이 화면을 쓸 수 있어요.</p>
      )}
      {state && state.enabled && !state.canEdit && (
        <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`} role="note">읽기 전용이에요. 키 담당 권한과 담당 고객사가 있는 계정만 키를 보고 바꿀 수 있어요. 운영자가 회원·서버 관리에서 '키 담당'을 켜 주세요.</p>
      )}

      {state && state.tenants.map((t) => {
        const byName = new Map(t.secrets.map((s) => [s.name, s]))
        const names = [...new Set([...state.suggested, ...t.secrets.map((s) => s.name)])]
        return (
          <section key={t.tenant} className="card space-y-3 p-6" aria-label={`${t.tenant} 키 목록`}>
            <h2 className="text-lg font-semibold">고객사 <span className="font-mono">{t.tenant}</span></h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-ink-500">
                  <tr><th className="py-2 pr-4 font-medium">키 이름</th><th className="py-2 pr-4 font-medium">상태</th><th className="py-2 font-medium">최근 변경</th></tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {names.map((n) => {
                    const s = byName.get(n)
                    return (
                      <tr key={n}>
                        <td className="py-2 pr-4 font-mono text-xs">{n}</td>
                        <td className="py-2 pr-4"><span className={`chip ${s ? 'bg-brand-50 text-brand-700' : 'bg-ink-100 text-ink-500'}`}>{hintKo(s)}</span></td>
                        <td className="py-2 text-xs text-ink-500">{s ? `${s.updated_by}, ${shortDateTime(s.updated_at)}` : '-'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )
      })}

      {state && open.length > 0 && (
        <section className="card space-y-2 p-6" aria-label="승인 대기 목록">
          <h2 className="text-lg font-semibold">승인 대기 목록</h2>
          <ul className="space-y-2 text-sm">
            {open.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{requestLineKo(r)}</span>
                <span className="text-xs text-ink-500">{r.requested_by}{r.requested_at ? ` · ${shortDateTime(r.requested_at)}` : ''}{r.reason ? ` · ${r.reason}` : ''}</span>
                {r.review_id && <Link className="text-xs underline" to={approvalLink(r.review_id)}>승인 화면 열기</Link>}
                {canTryApply(state, r) && (
                  <button className="btn-ghost text-xs" disabled={!canEdit || busy}
                    onClick={() => void run(async () => {
                      const out = writeResultKo(await applySecretRequest(r.id))
                      return out.kind === 'held' ? { ...out, text: '아직 승인되지 않았어요. 다른 담당자가 승인한 뒤 다시 눌러 주세요.' } : out
                    })}>
                    승인됨 → 적용
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {state && state.enabled && (
        <section className="card space-y-3 p-6" aria-label="키 넣기·바꾸기">
          <h2 className="text-lg font-semibold">키 넣기·바꾸기</h2>
          <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
            <li>추가는 키 담당자가 바로 해요. 교체·삭제는 요청한 사람과 다른 담당자가 승인 화면에서 승인해야 해요.</li>
            <li>값은 보낸 즉시 입력칸에서 지워지고, 서버에서 암호화돼요. 이력에는 누가·언제·무엇을 했는지만 남고 값은 남지 않아요.</li>
          </ul>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-ink-700">
              고객사
              <select className="field mt-1 block min-w-40" value={activeTenant} onChange={(e) => setTenant(e.target.value)} disabled={!canEdit}>
                {state.myTenants.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="text-xs text-ink-700">
              키 이름
              <input className="field mt-1 block w-72 font-mono" list="secret-name-list" maxLength={64} autoComplete="off" value={name}
                onChange={(e) => setName(e.target.value.toUpperCase())} disabled={!canEdit} />
              <datalist id="secret-name-list">{state.suggested.map((n) => <option key={n} value={n} />)}</datalist>
            </label>
            <label className="min-w-56 flex-1 text-xs text-ink-700">
              값 (추가·교체)
              <input className="field mt-1 block w-full font-mono" type="password" autoComplete="new-password" spellCheck={false}
                maxLength={4096} value={value} onChange={(e) => setValue(e.target.value)} disabled={!canEdit} />
            </label>
            <label className="min-w-40 text-xs text-ink-700">
              사유 (교체·삭제)
              <input className="field mt-1 block w-full" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} disabled={!canEdit} />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" disabled={!baseOk || !noValue('add')} onClick={() => submit('add')}>{busy ? '처리 중' : '추가(바로 저장)'}</button>
            <button className="btn-ghost text-xs" disabled={!baseOk || !noValue('replace') || !reason.trim()} onClick={() => submit('replace')}>교체 요청(승인 필요)</button>
            <button className="btn-ghost text-xs" disabled={!baseOk || !reason.trim()} onClick={() => submit('delete')}>삭제 요청(승인 필요)</button>
          </div>
          {canEdit && (nameIssue || valueIssue) && <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>{nameIssue ?? valueIssue}</p>}
          {!HUMAN_TOKEN_SET && <p className="text-xs text-ink-500">사람 토큰이 설정되지 않아 바꿀 수 없어요.</p>}
          {shown && (
            <p className={`rounded-xl px-3 py-2 text-sm ${shown.kind === 'done' ? TONE.ok : shown.kind === 'held' ? TONE.warn : TONE.bad}`} role="status">
              {shown.text}
              {shown.reviewId && <> <Link className="font-medium underline" to={approvalLink(shown.reviewId)}>사람 승인 화면 열기</Link></>}
            </p>
          )}
        </section>
      )}

      {state && state.history.length > 0 && (
        <section className="card space-y-2 p-6" aria-label="변경 이력">
          <h2 className="text-lg font-semibold">변경 이력</h2>
          <table className="w-full text-left text-xs">
            <thead className="text-ink-500">
              <tr><th className="py-1.5 pr-4 font-medium">언제</th><th className="py-1.5 font-medium">내용</th></tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {state.history.map((h, i) => (
                <tr key={`${h.at}-${i}`}><td className="py-1.5 pr-4 text-ink-500">{shortDateTime(h.at)}</td><td className="py-1.5 text-ink-700">{historyLineKo(h)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
