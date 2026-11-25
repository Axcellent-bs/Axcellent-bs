import { useEffect, useState } from "react";
import { safety } from "@/lib/safety";
import { approvalChipKo, secondsLeft } from "@/lib/videoApprovalTime";

// 영상 승인 대기 칩 (11/26): 안전 관문 보류 건 마감으로 "승인 대기 · 52분 남음".
// 마감을 못 읽으면 "승인 대기"만. 30초마다 남은 시간을 다시 센다(서버는 처음 한 번만 읽는다).
const TICK_MS = 30_000;

const deadlines = new Map<string, Promise<string | null>>();
function deadlineOf(reviewId: string): Promise<string | null> {
  let p = deadlines.get(reviewId);
  if (!p) {
    p = safety.review(reviewId).then((r) => r.expires_at ?? null, () => {
      deadlines.delete(reviewId); // 다음에 다시 읽는다
      return null;
    });
    deadlines.set(reviewId, p);
  }
  return p;
}

export function ApprovalDeadline({ reviewId, className = "" }: { reviewId: string | null | undefined; className?: string }) {
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!reviewId) return;
    let live = true;
    void deadlineOf(reviewId).then((v) => live && setExpiresAt(v));
    return () => { live = false; };
  }, [reviewId]);
  useEffect(() => {
    if (!expiresAt) return;
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, [expiresAt]);
  return (
    <span className={`chip bg-amber-50 text-[11px] text-amber-800 ${className}`} title={expiresAt ? `${new Date(expiresAt).toLocaleString("ko-KR")}까지 결정` : undefined}>
      {approvalChipKo(secondsLeft(expiresAt, now))}
    </span>
  );
}

/** 기한 지나 자동 취소된 영상: 칩 + [다시 승인 요청](같은 기록으로 영상 요청만 다시, 콘티·키프레임은 그대로) */
export function TimedOutRetry({ busy, onRetry }: { busy: boolean; onRetry: () => void }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="chip bg-red-50 text-[11px] text-red-700">기한 지나 취소됨</span>
      <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={onRetry}>
        {busy ? "요청 중…" : "다시 승인 요청"}
      </button>
      <span className="text-[11px] text-ink-500">콘티·키프레임은 다시 만들지 않아요(처음부터 다시 하지 않음)</span>
    </span>
  );
}
