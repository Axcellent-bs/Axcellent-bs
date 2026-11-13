import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApproverField, useApproverName } from "@/components/finance/ApproverName";
import { DASHBOARD_API_URL } from "@/lib/env";
import { FinanceApiError, finance, liveHumanTokenSet } from "@/lib/finance";
import { approvalLink, canDecideNow, decideErrorKo, remainingKo, remainingSeconds, type PublishState } from "@/lib/briefMedia";

// 기획안 카드 안 승인 패널. 게시 승인 대기(held)인 건의 전체 글·미리보기·AI 표시·남은 시간을 보여 주고,
// 사람이 "내용 확인"을 체크한 뒤 직접 누른 승인/거절만 기존 안전 장치 결정 경로(finance.decide →
// /api/safety/reviews/{id}/decision, 사람 토큰, 공용 토큰이면 승인자 이름 `decided_as`)로 보낸다.
// - 이 화면이 대신 승인하는 길은 없다: 자동 승인·모두 승인·기한 연장·기한 변경 기능이 없다.
// - 승인 뒤 게시는 서버의 기존 구독자·폴링이 잇는다. 이 화면은 결정만 보내고 곧 상태를 다시 읽는다.
// - 요청한 본인의 승인은 안전 장치가 거절한다(SELF_APPROVAL). 그 결과를 그대로 알려 준다.

const TICK_MS = 60_000; // 남은 시간은 1분 단위로 갱신
const QUICK_RELOAD_MS = 3_000; // 승인 직후 한 번 더 읽는다(기존 갱신 주기는 그대로)

export function ApprovalPanel({ state, onDone }: { state: PublishState; onDone: () => void }) {
  const reviewId = state.review_id;
  const [now, setNow] = useState(() => Date.now());
  const [reviewed, setReviewed] = useState(false);
  const [step, setStep] = useState<"idle" | "confirm" | "busy">("idle");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [approver, setApprover, approverName] = useApproverName();
  const timer = useRef<number | null>(null);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => () => { if (timer.current != null) window.clearTimeout(timer.current); }, []);

  if (state.status !== "held" || !reviewId) return null;
  const remaining = remainingSeconds(state.expires_at, now);
  const expired = remaining != null && remaining <= 0;
  const enabled = canDecideNow({ tokenSet: liveHumanTokenSet, reviewed, approver: approverName, remaining, busy: step === "busy" });
  const previewUrl = state.preview_url ? `${DASHBOARD_API_URL}${state.preview_url}` : null;

  async function decide(decision: "approved" | "rejected") {
    if (step === "busy" || !reviewId) return;
    setStep("busy");
    setMessage(null);
    try {
      await finance.decide(reviewId, decision, undefined, true, approverName);
      setMessage({ ok: true, text: decision === "approved" ? "승인했습니다. 게시는 서버가 이어서 진행합니다. 잠시 뒤 상태가 바뀝니다." : "거절했습니다. 게시하지 않습니다." });
      onDone();
      timer.current = window.setTimeout(onDone, QUICK_RELOAD_MS);
    } catch (e) {
      const err = e as FinanceApiError;
      setMessage({ ok: false, text: decideErrorKo(err.status, err.code) });
    } finally {
      setStep("idle");
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3" role="group" aria-label={`${state.proposal_id ?? ""} 게시 승인`}>
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-ink-900">게시 승인 대기</b>
        <span className={`chip ${expired ? "bg-red-50 text-red-800" : "bg-white text-amber-800"}`} aria-live="polite">{remainingKo(remaining)}</span>
        <span className="text-ink-500">시간 안에 아무도 누르지 않으면 자동으로 닫힙니다. 기한은 늘릴 수 없습니다.</span>
      </div>

      {previewUrl && (
        state.preview_kind === "video"
          ? <video className="max-h-80 rounded-xl bg-black" src={previewUrl} controls preload="metadata" aria-label="게시될 영상 미리보기" />
          : <img className="max-h-80 rounded-xl" src={previewUrl} alt="게시될 이미지 미리보기" />
      )}
      <div>
        <p className="text-ink-500">게시될 글 전체(수정할 수 없습니다)</p>
        <p className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-white px-2 py-1 text-ink-900">{state.caption ?? "서버가 글을 알려 주지 않았습니다."}</p>
      </div>
      <p className="text-ink-700">AI 생성 표시: <b>{state.ai_label ?? "서버가 표시 문구를 알려 주지 않았습니다"}</b></p>

      {!liveHumanTokenSet && <p className="text-amber-800">사람 결정 토큰(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</p>}
      {expired && <p className="text-red-800">승인 시간이 지났습니다. 곧 만료로 닫히며, 닫히면 사람이 다시 요청할 수 있습니다. 새로고침해 확인해 주세요.</p>}

      <label className="flex items-center gap-2 text-ink-900">
        <input type="checkbox" checked={reviewed} onChange={(e) => { setReviewed(e.target.checked); setStep("idle"); }} />
        글과 미리보기, AI 표시를 직접 확인했습니다.
      </label>
      <ApproverField id={`approval-approver-${state.proposal_id ?? reviewId}`} value={approver} onChange={setApprover} small />

      {step === "confirm" ? (
        <div className="space-y-2" role="group" aria-label="승인 확인">
          <p className="font-medium text-ink-900">승인하면 Instagram에 올라가며 되돌릴 수 없습니다. 승인할까요?</p>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" disabled={!enabled} onClick={() => void decide("approved")}>되돌릴 수 없음을 알고 승인</button>
            <button className="btn-ghost text-xs" onClick={() => setStep("idle")}>취소</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" disabled={!enabled} onClick={() => setStep("confirm")}>승인</button>
          <button className="btn-ghost text-xs" disabled={!enabled} onClick={() => void decide("rejected")}>거절</button>
          <Link className="font-medium text-brand-700 underline" to={approvalLink(reviewId)}>사람 승인 화면 열기</Link>
          {!enabled && <span className="text-ink-500">내용 확인 체크와 승인자 이름이 필요합니다.</span>}
        </div>
      )}
      {step === "busy" && <p className="text-ink-700" role="status">결정을 보내는 중입니다.</p>}
      {message && (
        <p className={`rounded-xl px-3 py-2 ${message.ok ? "bg-brand-50 text-brand-700" : "bg-red-50 text-red-800"}`} role={message.ok ? "status" : "alert"}>{message.text}</p>
      )}
    </div>
  );
}
