import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { shortDateTime } from "@/lib/format";
import { approvalLink } from "@/lib/briefMedia";
import { useVisibleInterval } from "@/lib/visibleInterval";
import {
  SOURCE_KO, canApply, checkDraft, historyLineKo, krw, limitHttpErrorKo, limitResultKo, lowerPathKo, loweringSummary, parseKrw, policyValueKo,
  readCostLimits, requestSummaryKo, reviewStatusKo, visibleRequests,
  type CostLimitsAnswer, type CostLimitsState, type LimitResult,
} from "@/lib/costLimits";
import type { LimitValues } from "@/lib/costLimitsApi";
import { getCostLimits, lowerCostLimits, raiseCostLimits, setLowerSelfApprove } from "@/lib/costLimitsApi";

// 영상 비용·정책: 영상 건당 상한과 월 예산을 화면에서 정하고, 안전 정책의 관련 값을 읽기 전용으로 보여 준다.
// - 값은 서버가 준 것만 보여 준다. 화면 검사는 안내용이고 최종 판단은 서버가 다시 한다.
// - 올리기: 다른 담당자의 사람 승인이 필요하고, 승인된 뒤 같은 요청을 다시 보내면 적용된다.
// - 낮추기: '하향 셀프 승인 허용'이 켜져 있으면 주의사항을 확인하고 바로 적용(이미 진행 중인 생성은 영향 없음),
//   꺼져 있으면 낮추기도 다른 담당자 승인이 필요하다(기본 꺼짐). 스위치는 정책 변경 권한(재무·총괄 담당)만 바꾼다.
// - 화면 값은 안전 정책 상한 안에서만 저장된다. 정책 상한 자체는 비용·안전 정책 창(/system/policy)에서 요청한다.
// - 바꾸는 사람은 비용 상한 변경 권한이 있는 운영자·관리자 계정만이다. 이름은 서버가 토큰에서 채운다.

const REFRESH_MS = 20_000;
const TONE = { ok: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-red-800" } as const;
const KIND_TONE: Record<LimitResult["kind"], string> = { done: TONE.ok, held: TONE.warn, failed: TONE.bad };
const POLICY_PAGE = "/system/policy";

export function CostLimitCard() {
  const [state, setState] = useState<CostLimitsState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoText, setVideoText] = useState("");
  const [monthlyText, setMonthlyText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LimitResult | null>(null);
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const [optReason, setOptReason] = useState("");

  const load = useCallback(async () => {
    try {
      const answer = await getCostLimits();
      const next = answer.outcome === "state" ? readCostLimits(answer.output) : null;
      setState(next);
      setError(next ? null : "비용 설정 응답을 읽지 못했습니다.");
    } catch (e) {
      setState(null);
      setError(limitHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code));
    } finally {
      setLoaded(true);
    }
  }, []);
  useVisibleInterval(load, REFRESH_MS);

  const draft = useMemo(() => ({ video_per_item_krw: parseKrw(videoText), monthly_budget_krw: parseKrw(monthlyText) }), [videoText, monthlyText]);
  const check = useMemo(() => (state ? checkDraft(state, draft) : null), [state, draft]);
  const canEdit = !!state?.viewer.can_edit && HUMAN_TOKEN_SET;
  const hasChange = !!check && check.changes.length > 0;
  const direction = check?.changes[0]?.direction ?? null;
  const selfApprove = state?.options.lower_self_approve === true;
  const reasonOk = reason.trim().length > 0 && reason.trim().length <= 200;
  const canSend = canEdit && !busy && hasChange && check!.errors.length === 0 && reasonOk;

  async function run(action: () => Promise<CostLimitsAnswer>, afterDone?: () => void) {
    if (busy) return; // 클릭 잠금
    setBusy(true);
    setResult(null);
    try {
      const shown = limitResultKo(await action());
      setResult(shown);
      if (shown.kind === "done") afterDone?.();
    } catch (e) {
      setResult({ kind: "failed", text: limitHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      setBusy(false);
      void load();
    }
  }

  const values = (): LimitValues => {
    const v: LimitValues = {};
    for (const c of check?.changes ?? []) v[c.field] = c.to;
    return v;
  };
  const clearForm = () => { setVideoText(""); setMonthlyText(""); setReason(""); };

  function submit() {
    if (!check || !canSend) return;
    if (direction === "down" && selfApprove) setConfirm(loweringSummary(check.changes, check.warnings));
    else if (direction === "down") void run(() => lowerCostLimits(values(), reason.trim(), false), clearForm);
    else void run(() => raiseCostLimits(values(), reason.trim()), clearForm);
  }
  function confirmLower() {
    setConfirm(null);
    void run(() => lowerCostLimits(values(), reason.trim(), true), clearForm);
  }

  const canEditPolicy = !!state?.viewer.can_edit_policy && HUMAN_TOKEN_SET;
  const video = state?.policy.items.find((p) => p.key === "video_cost_single");

  return (
    <section className="card space-y-3 p-6" aria-label="영상 비용·정책">
      <div>
        <h2 className="text-lg font-semibold">영상 비용·정책</h2>
        <p className="mt-1 text-xs text-ink-500">
          영상 한 건의 비용 상한과 월 예산을 정합니다. 이미지·BGM 건당 상한은 따로 두며 여기서는 보기만 합니다. 설정한 값은 서버 환경변수보다 우선하고, 이미 진행 중인 생성에는 영향이 없습니다.
          올리기는 다른 담당자의 승인이 필요하고, 낮추기는 아래 '하향 셀프 승인' 설정에 따라 바로 적용되거나 승인이 필요합니다.
        </p>
      </div>
      {!loaded && <p className="text-sm text-ink-500">불러오는 중</p>}
      {error && <p className="text-xs text-red-700" role="alert">{error}</p>}
      {state && check && (
        <>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className={`rounded-xl px-3 py-2 text-sm ${TONE.ok}`}>
              <p className="text-xs">영상 건당 상한</p>
              <p className="font-semibold">{krw(state.limits.video_per_item_krw)}</p>
              <p className="text-xs">{SOURCE_KO[state.limits.video_source]}</p>
            </div>
            <div className={`rounded-xl px-3 py-2 text-sm ${TONE.ok}`}>
              <p className="text-xs">월 예산 ({state.month || "이번 달"})</p>
              <p className="font-semibold">{krw(state.limits.monthly_budget_krw)}</p>
              <p className="text-xs">이번 달 누적 {krw(state.month_total_krw)} · {SOURCE_KO[state.limits.monthly_source]}</p>
            </div>
            <div className="rounded-xl bg-ink-50 px-3 py-2 text-sm text-ink-700">
              <p className="text-xs">이미지·BGM 건당 상한 (보기만)</p>
              <p className="font-semibold">{krw(state.limits.image_audio_per_item_krw)}</p>
              <p className="text-xs">서버 환경변수</p>
            </div>
          </div>
          {state.limits.updated_by && (
            <p className="text-xs text-ink-500">마지막 변경: {state.limits.updated_by}{state.limits.updated_at ? `, ${shortDateTime(state.limits.updated_at)}` : ""}</p>
          )}

          <div className="rounded-xl border border-ink-100 p-3">
            <h3 className="text-sm font-semibold">비용·정책 현황</h3>
            {state.policy.available ? (
              <ul className="mt-2 space-y-1 text-xs text-ink-700">
                {state.policy.items.map((p) => (
                  <li key={p.key} className="flex flex-wrap gap-x-2">
                    <span className="font-medium">{p.label}</span>
                    <span>{policyValueKo(p)}</span>
                    {p.scope === "daily_all_media" && <span className="chip">이미지·영상·BGM 합산</span>}
                    {p.note && <span className="text-ink-500">{p.note}</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-amber-800">안전 정책 값을 읽지 못했습니다. 저장은 정책 값을 확인할 수 있을 때만 됩니다.</p>
            )}
            <p className="mt-2 text-xs text-ink-500">
              화면에서 정한 값은 정책 상한 안에서만 저장됩니다. 정책 상한은 <Link className="underline" to={POLICY_PAGE}>비용·안전 정책 창</Link>에서 요청합니다(승인 뒤 적용).
              일일 합계는 이미지·영상·BGM을 합친 값이라 영상 건당 상한과는 별개입니다.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="text-sm font-semibold">상한 바꾸기</h3>
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-ink-700">
                영상 건당 상한(원)
                <input className="field mt-1 block w-40" inputMode="numeric" placeholder={String(state.limits.video_per_item_krw ?? "")} value={videoText} onChange={(e) => setVideoText(e.target.value)} disabled={!canEdit} />
              </label>
              <label className="text-xs text-ink-700">
                월 예산(원)
                <input className="field mt-1 block w-40" inputMode="numeric" placeholder={String(state.limits.monthly_budget_krw ?? "")} value={monthlyText} onChange={(e) => setMonthlyText(e.target.value)} disabled={!canEdit} />
              </label>
              <label className="min-w-56 flex-1 text-xs text-ink-700">
                사유(필수, 200자 이내)
                <input className="field mt-1 block w-full" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} disabled={!canEdit} />
              </label>
              <button className="btn-primary text-xs" disabled={!canSend} onClick={submit}>
                {busy ? "처리 중" : direction === "up" ? "상향 요청(다른 담당자 승인 필요)" : selfApprove ? "낮추기(확인 후 바로 적용)" : "낮추기 요청(다른 담당자 승인 필요)"}
              </button>
            </div>
            <p className="text-xs text-ink-500">비워 둔 칸은 바꾸지 않습니다. 범위: {krw(state.bounds.min_krw)} 이상, 영상 건당 {krw(state.bounds.video_item_max_krw)} 이하{video && video.max_allowed !== null ? `(정책상 ${krw(video.max_allowed)} 이하)` : ""}, 월 예산 {krw(state.bounds.monthly_max_krw)} 이하.</p>
            {hasChange && check.errors.map((m) => <p key={m} className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>{m}</p>)}
            {hasChange && check.errors.length === 0 && check.warnings.map((m) => <p key={m} className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>{m}</p>)}
            <p className="text-xs text-ink-500">{lowerPathKo(selfApprove)}</p>
            {check.policyCeiling !== null && (
              <Link className="text-xs font-medium underline" to={POLICY_PAGE}>비용·안전 정책 창에서 정책 상한 변경 요청하기</Link>
            )}
            {!state.viewer.can_edit && <p className="text-xs text-ink-500">비용 상한 변경 권한이 없는 계정입니다. 관리자가 회원·서버 관리에서 권한을 켜야 바꿀 수 있습니다.</p>}
            {!HUMAN_TOKEN_SET && <p className="text-xs text-ink-500">사람 토큰이 설정되지 않아 바꿀 수 없습니다.</p>}
          </div>
        </>
      )}

      {state && (
        <div className="rounded-xl border border-ink-100 p-3 text-xs text-ink-700">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold">하향 셀프 승인 허용</span>
            <button type="button" role="switch" aria-checked={selfApprove} aria-label="하향 셀프 승인 허용"
              disabled={!canEditPolicy || busy || !optReason.trim()}
              onClick={() => void run(() => setLowerSelfApprove(!selfApprove, optReason.trim()), () => setOptReason(""))}
              className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${selfApprove ? "bg-brand-600" : "bg-ink-100"}`}>
              <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${selfApprove ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
            <span>{selfApprove ? "켜짐: 낮추기는 확인 뒤 바로 적용" : "꺼짐(기본): 낮추기도 다른 담당자 승인 필요"}</span>
            {state.options.updated_by && <span className="text-ink-500">마지막 변경 {state.options.updated_by}{state.options.updated_at ? `, ${shortDateTime(state.options.updated_at)}` : ""}</span>}
          </div>
          <label className="mt-2 block">
            바꿀 때 사유(필수)
            <input className="field mt-1 block w-full max-w-md" maxLength={200} value={optReason} onChange={(e) => setOptReason(e.target.value)} disabled={!canEditPolicy} />
          </label>
          <p className="mt-1 text-ink-500">이 설정은 재무·총괄 담당(정책 변경 권한이 있는 계정)만 바꿀 수 있습니다.{!state.viewer.can_edit_policy ? " 지금 계정에는 그 권한이 없습니다." : ""}</p>
        </div>
      )}

      {confirm && (
        <div role="dialog" aria-modal="true" aria-label="낮추기 확인" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">낮추기 전에 확인해 주세요</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">{confirm.map((l) => <li key={l}>{l}</li>)}</ul>
          <div className="mt-3 flex gap-2">
            <button className="btn-primary text-xs" disabled={busy} onClick={confirmLower}>확인했습니다. 바로 적용</button>
            <button className="btn-ghost text-xs" onClick={() => setConfirm(null)}>취소</button>
          </div>
        </div>
      )}

      {result && (
        <p className={`rounded-xl px-3 py-2 text-sm ${KIND_TONE[result.kind]}`} role="status">
          {result.text}
          {result.policyKey && <> <Link className="font-medium underline" to={POLICY_PAGE}>비용·안전 정책 창 열기</Link></>}
          {result.reviewId && <> <Link className="font-medium underline" to={approvalLink(result.reviewId)}>사람 승인 화면 열기</Link></>}
        </p>
      )}

      {state && visibleRequests(state.requests).length > 0 && (
        <div className="border-t border-ink-100 pt-3">
          <h3 className="text-sm font-semibold">승인 목록</h3>
          <ul className="mt-2 space-y-2 text-xs text-ink-700">
            {visibleRequests(state.requests).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{requestSummaryKo(r)}</span>
                <span className="chip">{reviewStatusKo(r)}</span>
                <span className="text-ink-500">{r.requested_by}{r.requested_at ? ` · ${shortDateTime(r.requested_at)}` : ""}{r.reason ? ` · ${r.reason}` : ""}</span>
                {r.review_status === "pending" && <Link className="underline" to={approvalLink(r.id)}>승인 화면 열기</Link>}
                {canApply(r) && (
                  <button className="btn-ghost text-xs" disabled={!canEdit || busy} onClick={() => void run(() =>
                    r.kind === "raise"
                      ? raiseCostLimits({ video_per_item_krw: r.payload.video_per_item_krw, monthly_budget_krw: r.payload.monthly_budget_krw }, r.reason)
                      : lowerCostLimits({ video_per_item_krw: r.payload.video_per_item_krw, monthly_budget_krw: r.payload.monthly_budget_krw }, r.reason, false))}>
                    승인됨 → 적용
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {state && state.history.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer">최근 변경 내력 {state.history.length}건</summary>
          <ul className="mt-2 space-y-1">
            {state.history.map((h, i) => <li key={`${h.at}-${i}`}>{shortDateTime(h.at)} · {h.by} · {historyLineKo(h)}</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}
