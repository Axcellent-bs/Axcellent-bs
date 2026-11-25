import { useState } from "react";
import { ApiError } from "@/lib/apiError";
import { reapplyOverlay } from "@/lib/creativeRecords";
import { CAPTION_MAX, canReapply, captionDrafts, overlayChip, reapplyBody, type CaptionShot, type OverlayState } from "@/lib/videoOverlay";

// 영상 칸 아래: 자막·엔드카드 상태 칩 + [원본 보기] + [자막 고쳐서 다시 얹기]. render.overlay가 없으면 그리지 않는다.
// 다시 얹기 요청이 404면(서버가 아직 모름) 버튼을 숨기고 아무것도 보이지 않는다.

const TONE = { ok: "bg-emerald-50 text-emerald-800", busy: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800" } as const;

export function OverlayControls({ recordId, overlay, shots, showOriginal, canToggle, onToggle, onChanged }: {
  recordId: string;
  overlay?: OverlayState | null;
  shots?: readonly CaptionShot[] | null;
  showOriginal: boolean;
  canToggle: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const chip = overlayChip(overlay);
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<{ no: number; caption_ko: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  if (!chip) return null;

  const open = () => { setDrafts(captionDrafts(shots)); setError(null); setEditing(true); };
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await reapplyOverlay(recordId, reapplyBody(drafts).captions);
      setEditing(false);
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { setUnsupported(true); setEditing(false); }
      else setError(e instanceof Error ? e.message : "자막을 다시 얹지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  const showReapply = !unsupported && canReapply(overlay, shots);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`chip ${TONE[chip.tone]}`} role="status">{chip.text}</span>
        {canToggle && <button type="button" className="btn-ghost text-xs" onClick={onToggle}>{showOriginal ? "자막 입힌 영상 보기" : "원본 보기"}</button>}
        {showReapply && !editing && <button type="button" className="btn-ghost text-xs" onClick={open}>자막 고쳐서 다시 얹기</button>}
      </div>
      {editing && (
        <div className="space-y-2 rounded-xl bg-ink-50 p-3">
          <p className="text-xs text-ink-700">장면마다 자막을 고쳐요. 한 줄에 {CAPTION_MAX}자까지 쓸 수 있어요.</p>
          {drafts.map((d, i) => (
            <label key={d.no} className="flex items-center gap-2 text-xs text-ink-700">
              <span className="w-12 shrink-0">장면 {d.no}</span>
              <input className="field text-sm" maxLength={CAPTION_MAX} value={d.caption_ko} disabled={busy} aria-label={`장면 ${d.no} 자막`}
                onChange={(e) => { const v = e.target.value; setDrafts((all) => all.map((x, j) => (j === i ? { ...x, caption_ko: v } : x))); }} />
            </label>
          ))}
          <div className="flex gap-2">
            <button type="button" className="btn-primary text-xs" disabled={busy} onClick={() => void send()}>{busy ? "요청 중…" : "다시 얹기"}</button>
            <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => setEditing(false)}>취소</button>
          </div>
          {error && <p className="text-xs text-accent" role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}
