import { useState } from "react";
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { orchestrator } from "@/lib/orchestrator";
import { RETRY_LIMIT_KO, canRetryStep, retryButton, retryCountLine, retryError, retryLimited } from "@/lib/requestRetry";
import type { WorkRequestStep } from "@/lib/workRequests";

/** 11/27 실패·취소·막힌 단계 아래 [이 단계부터 다시] + "다시 시도 n/3" (대화방 의뢰 카드·받은 의뢰 상세 같이 씀).
 *  앞 단계 결과는 그대로 두고 이 단계부터 다시 돈다. 비용 드는 단계는 이번에도 사람 승인을 거친다 */
export function StepRetry({ requestId, stepId, skill, step, credits, onDone, small }: {
  requestId: string; stepId: string; skill: string; step: WorkRequestStep | undefined;
  credits?: number | null; onDone: () => void; small?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!canRetryStep(step)) return null;
  const count = retryCountLine(step);
  const limited = retryLimited(step);
  const retry = () => {
    setBusy(true);
    setError(null);
    orchestrator.retryRequestStep(requestId, stepId)
      .then(onDone, (e: unknown) => {
        const err = retryError(e);
        setError(err.text);
        if (err.reload) onDone();
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className="space-y-1">
      <span className="flex flex-wrap items-center gap-2">
        {!limited && (
          <button type="button" className={`btn-primary ${small ? "text-xs" : "btn-card"}`} disabled={busy || !SAFETY_HUMAN_TOKEN}
            title={SAFETY_HUMAN_TOKEN ? undefined : "로그인이 필요해요"} onClick={retry}>
            {busy ? "다시 시도하는 중…" : retryButton(skill, credits)}
          </button>
        )}
        {count && <span className="text-[12.5px] tabular-nums text-ink-500">{count}</span>}
      </span>
      {limited && <p className="text-[13px] text-amber-800">{RETRY_LIMIT_KO}</p>}
      {error && <p role="alert" className="text-[13px] text-red-700">{error}</p>}
    </div>
  );
}
