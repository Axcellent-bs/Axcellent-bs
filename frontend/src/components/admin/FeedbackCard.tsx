import { useState } from "react";
import { Stars } from "@/components/Stars";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import type { Conversation } from "@/lib/types";

// 상담 콘솔의 고객 평가: 해결로 닫은 문의에 고객이 남긴 별점·해결 여부·후기를 보여 준다.
// 아직 평가가 없는 해결 문의는 고객 대신 시험으로 남길 수 있다(실제 고객 화면은 쇼핑몰이 직접 보낸다).

const STATUS_KO = { visible: "공개", flagged: "확인 대기", hidden: "숨김" } as const;
const FLAG_KO: Record<string, string> = {
  PERSONAL_INFO: "개인정보", LINK_OR_CONTACT: "링크·연락 유도", PROFANITY: "비속어", PROMOTION: "광고성", REPEATED_CHARS: "반복 문자", CS_FOLLOW_UP: "선제 연락 대상",
};

export function FeedbackCard({ conv, onSaved }: { conv: Conversation; onSaved: (c: Conversation) => void }) {
  const [rating, setRating] = useState(0);
  const [solved, setSolved] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fb = conv.feedback;

  async function save() {
    if (!rating || solved === null) return;
    setBusy(true); setError(null);
    try {
      onSaved(await api.admin.submitFeedback(conv.id, { rating, resolved: solved, comment: comment.trim() }));
      setRating(0); setSolved(null); setComment("");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <section aria-label="고객 평가">
      <h3 className="panel-title mb-2">고객 평가</h3>
      {fb ? (
        <div className="space-y-1.5 rounded-xl border border-ink-100 bg-surface p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Stars value={fb.rating} size={13} />
            <span className={`chip ${fb.resolved ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>{fb.resolved ? "해결됐다고 답함" : "해결 안 됐다고 답함"}</span>
            <span className="chip bg-ink-100 text-ink-700">{STATUS_KO[fb.status]}</span>
            {fb.source === "console" && <span className="chip bg-ink-100 text-ink-500">시험 평가</span>}
            <span className="ml-auto text-ink-500">{dateStr(fb.created_at)}</span>
          </div>
          {fb.comment ? <p className="whitespace-pre-wrap leading-relaxed text-ink-700">{fb.comment}</p> : <p className="text-ink-500">남긴 글이 없습니다.</p>}
          {fb.flags.map((f) => <p key={f.code} className={f.info ? "text-red-800" : "text-amber-800"}>· <b>{FLAG_KO[f.code] ?? f.code}</b> {f.message}</p>)}
        </div>
      ) : conv.status !== "resolved" ? (
        <p className="rounded-xl bg-surface px-3 py-2 text-xs text-ink-500">문의를 해결로 닫으면 고객이 평가를 남길 수 있습니다.</p>
      ) : (
        <div className="space-y-2 rounded-xl border border-dashed border-ink-100 bg-surface p-3 text-xs">
          <p className="text-ink-500">아직 고객 평가가 없습니다. 고객 대신 시험으로 남길 수 있습니다.</p>
          <div className="flex items-center gap-1" role="radiogroup" aria-label="별점">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} type="button" role="radio" aria-checked={rating === n} aria-label={`${n}점`} onClick={() => setRating(n)}
                className={`rounded-md px-1.5 py-0.5 text-sm ${rating >= n ? "text-amber-600" : "text-ink-300"}`}>★</button>
            ))}
            <span className="ml-1 text-ink-500">{rating ? `${rating}점` : "별점을 고르세요"}</span>
          </div>
          <div className="flex items-center gap-3" role="radiogroup" aria-label="문제 해결 여부">
            <span className="text-ink-500">문제가 해결됐나요?</span>
            {([true, false] as const).map((v) => (
              <label key={String(v)} className="flex items-center gap-1"><input type="radio" name={`solved-${conv.id}`} checked={solved === v} onChange={() => setSolved(v)} />{v ? "예" : "아니오"}</label>
            ))}
          </div>
          <input className="field py-1 text-xs" placeholder="한 줄 후기 (선택, 300자)" maxLength={300} value={comment} onChange={(e) => setComment(e.target.value)} aria-label="한 줄 후기" />
          {error && <p role="alert" className="rounded-lg bg-red-50 px-2 py-1 font-medium text-red-800">{error}</p>}
          <button type="button" className="btn-ghost w-full py-1.5" disabled={busy || !rating || solved === null} onClick={save}>{busy ? "남기는 중…" : "고객 평가 남기기 (시험)"}</button>
        </div>
      )}
    </section>
  );
}
