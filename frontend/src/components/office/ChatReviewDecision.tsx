import { useCallback, useEffect, useState } from "react";
import { ApproverField, useApproverName } from "@/components/finance/ApproverName";
import { FinanceApiError, finance, liveHumanTokenSet, type Review } from "@/lib/finance";
import { canDecideNow, decideErrorKo, remainingKo, remainingSeconds } from "@/lib/briefMedia";
import { safety } from "@/lib/safety";

// 대화방 의뢰 카드 안 C 보류 결정 (11/26). 영상 만들기·유튜브·인스타 올리기 승인을 대화방에서 바로 한다.
// - 결정 경로는 기존 그대로: finance.decide → /api/safety/reviews/{id}/decision (사람 토큰, 공용 토큰이면 승인자 이름)
// - 요청한 본인의 승인은 안전 장치가 거절한다(SELF_APPROVAL). 그 결과를 그대로 알려 준다
// - "내용을 확인했습니다"를 체크해야 버튼이 켜지고, 승인은 한 번 더 묻는다. 자동 승인·기한 연장은 없다

const TICK_MS = 60_000;

export function ChatReviewDecision({ reviewId, what, onDone }: { reviewId: string; what: string; onDone?: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [reviewed, setReviewed] = useState(false);
  const [step, setStep] = useState<"idle" | "confirm" | "busy">("idle");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [approver, setApprover, approverName, decidedAs] = useApproverName();
  const load = useCallback(() => {
    safety.review(reviewId).then(setReview, () => setReview(null));
  }, [reviewId]);
  useEffect(load, [load]);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, []);

  if (!review || (review.status !== "pending" && !message)) return null;
  const remaining = remainingSeconds(review.expires_at, now);
  const enabled = canDecideNow({ tokenSet: liveHumanTokenSet, reviewed, approver: approverName, remaining, busy: step === "busy" });

  async function decide(decision: "approved" | "rejected") {
    if (step === "busy") return;
    setStep("busy");
    setMessage(null);
    try {
      await finance.decide(reviewId, decision, undefined, true, decidedAs ?? undefined);
      setMessage({ ok: true, text: decision === "approved" ? `승인했어요. ${what}는 서버가 이어서 진행해요.` : "거절했어요. 진행하지 않아요." });
      load();
      onDone?.();
    } catch (e) {
      const err = e as FinanceApiError;
      setMessage({ ok: false, text: decideErrorKo(err.status, err.code) });
    } finally {
      setStep("idle");
    }
  }

  return (
    <div className="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs" role="group" aria-label={`${what} 승인`}>
      {review.status === "pending" && (
        <>
          <p className="flex flex-wrap items-center gap-1.5">
            <b className="text-ink-900">{what} 승인</b>
            <span className="chip bg-white text-[11px] text-amber-800">{remainingKo(remaining)}</span>
            <span className="text-ink-500">요청한 계정과 다른 계정만 승인할 수 있어요</span>
          </p>
          {!liveHumanTokenSet && <p className="text-amber-800">로그인하면 결정 버튼이 켜져요.</p>}
          <label className="flex items-center gap-1.5 text-ink-900">
            <input type="checkbox" checked={reviewed} onChange={(e) => { setReviewed(e.target.checked); setStep("idle"); }} />
            내용을 직접 확인했어요
          </label>
          <ApproverField id={`chat-approver-${reviewId}`} value={approver} onChange={setApprover} small />
          {step === "confirm" ? (
            <span className="flex flex-wrap items-center gap-1.5">
              <span className="text-ink-900">승인하면 되돌릴 수 없어요. 승인할까요?</span>
              <button type="button" className="btn-primary btn-card" disabled={!enabled} onClick={() => void decide("approved")}>승인</button>
              <button type="button" className="btn-ghost btn-card" onClick={() => setStep("idle")}>취소</button>
            </span>
          ) : (
            <span className="flex flex-wrap gap-1.5">
              <button type="button" className="btn-primary btn-card" disabled={!enabled} onClick={() => setStep("confirm")}>승인하기</button>
              <button type="button" className="btn-ghost btn-card" disabled={!enabled} onClick={() => void decide("rejected")}>거절</button>
            </span>
          )}
        </>
      )}
      {message && <p role={message.ok ? "status" : "alert"} className={message.ok ? "text-emerald-800" : "text-red-700"}>{message.text}</p>}
    </div>
  );
}
