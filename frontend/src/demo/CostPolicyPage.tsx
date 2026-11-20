import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { PageHead } from '@/components/connect/parts'
import { approvalLink } from '@/lib/briefMedia'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { shortDateTime } from '@/lib/format'
import { useVisibleInterval } from '@/lib/visibleInterval'
import {
  canApply, historyLineKo, openRequests, parseValue, policyHttpErrorKo, readCostPolicy, requestLineKo, requestProblem, requestResultKo,
  requestStatusKo, sourceKo, unitKo, valueKo, type CostPolicyState,
} from '@/lib/costPolicy'
import { applyPolicyRequest, getCostPolicy, requestPolicyValue } from '@/lib/costPolicyApi'

// 사용량·안전 정책 (관리자 /system/policy). 안전 정책의 기준값(영상 길이)을 보고, 바꿔 달라는 요청을 올린다.
// - 원화 비용 기준(건당·하루 합계·0원)은 없다. 우리 키 월 사용량 예산(영상 초·이미지 장·BGM 곡)은 서버 설정(MARKETING_QUOTA_*)으로 정한다(마케팅 화면 칸은 2026-11-20에 뺐다).
// - 값·기본값·절대 상한은 서버가 준 것만 보여 준다. 절대 상한은 코드 상수라 승인으로도 넘길 수 없다.
// - 요청은 사람 승인(요청자와 다른 사람)이 필요하고, 승인 뒤 '승인됨 → 적용'을 눌러야 파일에 반영된다. 안전 서버가 재시작 없이 다시 읽는다.
// - 에이전트·일반회원·공용 토큰은 못 바꾼다.
const REFRESH_MS = 20_000
const TONE = { ok: 'bg-brand-50 text-brand-700', warn: 'bg-amber-50 text-amber-800', bad: 'bg-red-50 text-red-800' } as const

type Shown = { kind: 'done' | 'held' | 'failed'; text: string; reviewId?: string | null }

export default function CostPolicyPage() {
  const [state, setState] = useState<CostPolicyState | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ruleKey, setRuleKey] = useState('')
  const [valueText, setValueText] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState<Shown | null>(null)

  const load = useCallback(async () => {
    try {
      const next = readCostPolicy(await getCostPolicy())
      setState(next)
      setError(next ? null : '정책 응답을 읽지 못했습니다.')
    } catch (e) {
      setState(null)
      setError(policyHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code))
    } finally {
      setLoaded(true)
    }
  }, [])
  useVisibleInterval(load, REFRESH_MS)

  const rule = useMemo(() => state?.items.find((i) => i.key === (ruleKey || state.items[0]?.key)), [state, ruleKey])
  const value = parseValue(valueText)
  const problem = state ? requestProblem(rule, value, reason) : null
  const canEdit = !!state?.viewer.can_edit_policy && state.editable && HUMAN_TOKEN_SET
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
      setShown({ kind: 'failed', text: policyHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) })
    } finally {
      setBusy(false)
      void load()
    }
  }

  return (
    <div className="space-y-6 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="사용량·안전 정책">
        영상 길이 같은 안전 정책 기준값을 보고, 바꿔 달라는 요청을 올려요. 원화 비용 기준은 없고, 우리 키로 만들 때의 월 사용량 예산은 마케팅 화면에서 정해요. 값은 승인과 적용을 거쳐야 바뀌어요.
      </PageHead>

      {!loaded && <p className="text-sm text-ink-500">불러오는 중</p>}
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}

      {state && (
        <section className="card space-y-3 p-6" aria-label="정책 현황">
          <h2 className="text-lg font-semibold">정책 현황</h2>
          {!state.editable && <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>서버에 정책 변경 파일이 설정되지 않아 지금은 보기만 됩니다. 설치 담당자가 먼저 설정해야 요청할 수 있어요.</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-ink-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">항목</th>
                  <th className="py-2 pr-4 font-medium">현재 값</th>
                  <th className="py-2 pr-4 font-medium">기본값(정책 파일)</th>
                  <th className="py-2 pr-4 font-medium">범위(하한~절대 상한)</th>
                  <th className="py-2 pr-4 font-medium">출처</th>
                  <th className="py-2 font-medium">마지막 변경</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {state.items.map((r) => (
                  <tr key={r.key}>
                    <td className="py-2 pr-4">
                      <span className="font-medium">{r.label}</span>
                    </td>
                    <td className="py-2 pr-4 font-semibold">{valueKo(r.value, r.unit)}</td>
                    <td className="py-2 pr-4 text-ink-700">{valueKo(r.default, r.unit)}</td>
                    <td className="py-2 pr-4 text-ink-700">{valueKo(r.min, r.unit)}~{valueKo(r.absolute_max, r.unit)}</td>
                    <td className="py-2 pr-4 text-ink-700">{sourceKo(r)}</td>
                    <td className="py-2 text-xs text-ink-500">{r.updated_by ? `${r.updated_by}${r.updated_at ? `, ${shortDateTime(r.updated_at)}` : ''}` : '-'}</td>
                  </tr>
                ))}
                {!state.items.length && <tr><td colSpan={6} className="py-4 text-center text-ink-500">정책 항목이 없어요</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-ink-500">절대 상한과 하한은 코드 상수라 승인으로도 넘길 수 없어요(넘으려면 코드를 바꿔야 해요).</p>
        </section>
      )}

      {state && open.length > 0 && (
        <section className="card space-y-2 p-6" aria-label="승인 목록">
          <h2 className="text-lg font-semibold">승인 목록</h2>
          <ul className="space-y-2 text-sm">
            {open.map((r) => (
              <li key={r.review_id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{requestLineKo(state.items, r)}</span>
                <span className="chip">{requestStatusKo(r.status)}</span>
                <span className="text-xs text-ink-500">{r.requested_by}{r.requested_at ? ` · ${shortDateTime(r.requested_at)}` : ''}{r.reason ? ` · ${r.reason}` : ''}</span>
                {r.status === 'pending' && <Link className="text-xs underline" to={approvalLink(r.review_id)}>승인 화면 열기</Link>}
                {canApply(state, r) && (
                  <button className="btn-ghost text-xs" disabled={!canEdit || busy}
                    onClick={() => void run(async () => { await applyPolicyRequest(r.review_id); return { kind: 'done', text: '정책 값을 적용했습니다. 안전 서버가 재시작 없이 다시 읽습니다.' } })}>
                    승인됨 → 적용
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {state && (
        <section className="card space-y-3 p-6" aria-label="정책 변경 요청">
          <h2 className="text-lg font-semibold">정책 변경 요청</h2>
          <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
            <li>승인은 요청한 사람과 다른 사람이 해야 해요. 승인 화면에서 승인하면 이 창에 '승인됨 → 적용'이 나타나요.</li>
            <li>적용은 승인 뒤 '적용' 버튼을 눌러야 해요. 바뀐 값은 안전 서버가 재시작 없이 다시 읽어요.</li>
            <li>에이전트·일반회원·공용 토큰은 못 바꿔요. 정책 변경 권한이 있는 운영자·관리자만 요청해요.</li>
          </ul>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-ink-700">
              항목
              <select className="field mt-1 block min-w-56" value={rule?.key ?? ''} onChange={(e) => setRuleKey(e.target.value)} disabled={!canEdit}>
                {state.items.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-ink-700">
              바라는 값({rule ? unitKo(rule.unit) : ''})
              <input className="field mt-1 block w-32" inputMode="numeric" placeholder={rule ? String(rule.value) : ''} value={valueText} onChange={(e) => setValueText(e.target.value)} disabled={!canEdit} />
            </label>
            <label className="min-w-56 flex-1 text-xs text-ink-700">
              사유(필수, 200자 이내)
              <input className="field mt-1 block w-full" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} disabled={!canEdit} />
            </label>
            <button className="btn-primary text-xs" disabled={!canEdit || busy || !!problem || !rule}
              onClick={() => rule && void run(async () => {
                const out = requestResultKo(await requestPolicyValue(rule.key, value as number, reason.trim()))
                return out
              }, () => { setValueText(''); setReason('') })}>
              {busy ? '처리 중' : '변경 요청(승인 필요)'}
            </button>
          </div>
          {rule && <p className="text-xs text-ink-500">범위: {valueKo(rule.min, rule.unit)}~{valueKo(rule.absolute_max, rule.unit)}, 지금 {valueKo(rule.value, rule.unit)}</p>}
          {canEdit && (valueText || reason) && problem && <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>{problem}</p>}
          {!state.viewer.can_edit_policy && <p className="text-xs text-ink-500">정책 변경 권한이 없는 계정이에요. 운영자나 정책 담당 관리자가 회원·서버 관리에서 권한을 켜야 해요.</p>}
          {!HUMAN_TOKEN_SET && <p className="text-xs text-ink-500">로그인이 필요해요. 로그인하면 바꿀 수 있어요.</p>}
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
              {[...state.history].reverse().map((h, i) => (
                <tr key={`${h.at}-${i}`}><td className="py-1.5 pr-4 text-ink-500">{shortDateTime(h.at)}</td><td className="py-1.5 text-ink-700">{historyLineKo(state.items, h)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
