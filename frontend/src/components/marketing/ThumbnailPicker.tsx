import { useState } from "react";
import { Link } from "react-router";
import { useAuthedMedia } from "@/lib/authedMedia";
import {
  THUMB_CONFIRM_KO, THUMB_PENDING, THUMB_STATUS_KO, canCommitThumb, needsThumbConfirm, requestThumbnails, selectThumbnail,
  thumbAction, thumbCells, thumbErrorText, thumbUrl, thumbsLocked, type ThumbCell, type ThumbnailsState,
} from "@/lib/creativeThumbnails";

// 제작 기록 영상의 "썸네일" 칸 (RenderPanel 안). 제작 기록 상세·SNS·미디어 만들기 탭 결과·AI 대화방 카드가 같은 RenderPanel을 써서
// 세 곳 모두 이 칸이 보인다. 후보 3장은 Higgsfield로 만든 그림(AI 표시 포함)이고, 만들기는 늘 사람 결정 뒤에만 한다.
// 고르지 않으면 첫 장이 쓰이고 유튜브 올리기 승인에 같이 들어간다. 유튜브 승인 대기 중에 바꾸면 그 승인이 풀린다(확인 상자).

function Thumb({ recordId, cell, wide, onPick }: { recordId: string; cell: ThumbCell; wide: boolean; onPick: () => void }) {
  const { src, failed } = useAuthedMedia(thumbUrl(recordId, cell.media_id));
  const ring = cell.selected ? "border-accent ring-2 ring-accent/40" : "border-ink-100";
  const box = wide ? "aspect-video w-40" : "aspect-[9/16] w-24";
  return (
    <li>
      <button type="button" onClick={onPick} aria-pressed={cell.selected}
        aria-label={`썸네일 후보 ${cell.kind}${cell.chosen ? " (정해진 썸네일)" : ""}`}
        className={`relative block overflow-hidden rounded-lg border-2 bg-ink-50 ${ring}`}>
        {src ? <img src={src} alt="" className={`${box} object-cover`} />
          : <span className={`grid ${box} place-items-center text-[11px] text-ink-500`}>{failed ? "못 불러옴" : "불러오는 중…"}</span>}
        {cell.chosen && <span className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-white" aria-hidden="true">✓</span>}
        {cell.kind && <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] text-white">{cell.kind}</span>}
      </button>
    </li>
  );
}

export function ThumbnailPicker({ recordId, state, hasVideo, youtubeStatus, instagramStatus, onChanged }: {
  recordId: string; state?: ThumbnailsState | null; hasVideo: boolean; youtubeStatus?: string | null; instagramStatus?: string | null; onChanged: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"make" | "pick" | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const cells = thumbCells(state, selected);
  const locked = thumbsLocked(youtubeStatus, instagramStatus);
  const action = thumbAction(state, hasVideo, locked);
  const status = state?.status ?? "";
  const target = cells.find((c) => c.selected);
  const fail = (e: unknown, fallback: string) => setNote({ tone: "err", text: thumbErrorText(e instanceof Error ? e.message : null) ?? fallback });

  const make = () => {
    setBusy("make");
    setNote(null);
    requestThumbnails(recordId, action === "remake")
      .then(() => onChanged())
      .catch((e: unknown) => fail(e, "썸네일 만들기를 요청하지 못했어요"))
      .finally(() => setBusy(null));
  };
  const commit = () => {
    if (!target) return;
    setBusy("pick");
    setNote(null);
    selectThumbnail(recordId, target.media_id)
      .then((r) => {
        setConfirming(false);
        setSelected(null);
        setNote({ tone: "ok", text: r.approvals_reset.length ? "썸네일을 바꿨어요. 유튜브·인스타 올리기 승인을 다시 요청해 주세요." : "썸네일을 정했어요." });
        onChanged();
      })
      .catch((e: unknown) => fail(e, "썸네일을 정하지 못했어요"))
      .finally(() => setBusy(null));
  };
  const onCommit = () => (needsThumbConfirm(cells, youtubeStatus, instagramStatus) ? setConfirming(true) : commit());

  if (!hasVideo && !state?.status) return null;
  return (
    <section className="space-y-2 border-t border-ink-100 pt-3" aria-label="썸네일">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-700">
        썸네일
        {status && <span className={`chip ${status === "done" ? "bg-emerald-50 text-emerald-800" : THUMB_PENDING.has(status) ? "bg-brand-50 text-brand-700" : "bg-amber-50 text-amber-800"}`}>{THUMB_STATUS_KO[status] ?? status}</span>}
        <span className="font-normal text-ink-500">· Higgsfield로 만든 3장(AI 생성 표시 포함). 고르지 않으면 첫 장이 쓰여요</span>
        {action && (
          <button type="button" className="btn-ghost ml-auto text-xs" disabled={busy !== null} onClick={make}>
            {busy === "make" ? "요청 중…" : action === "remake" ? "다시 만들기 (사람 결정)" : "썸네일 3장 만들기 (사람 결정)"}
          </button>
        )}
      </div>
      {cells.length > 0 && (
        <>
          <ul className="flex flex-wrap gap-2">
            {cells.map((c) => <Thumb key={c.media_id} recordId={recordId} cell={c} wide={state?.aspect !== "9:16"} onPick={() => { setSelected(c.media_id); setConfirming(false); }} />)}
          </ul>
          {locked ? <p className="text-xs text-ink-500">이미 올린 영상이라 썸네일을 바꿀 수 없어요.</p>
            : confirming ? (
              <div role="alertdialog" aria-label="썸네일 바꾸기 확인" className="space-y-2 rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <p>{THUMB_CONFIRM_KO}</p>
                <span className="flex flex-wrap gap-1.5">
                  <button type="button" className="btn-primary btn-card" disabled={busy !== null} onClick={commit}>바꾸기</button>
                  <button type="button" className="btn-ghost btn-card" disabled={busy !== null} onClick={() => setConfirming(false)}>그대로 두기</button>
                </span>
              </div>
            ) : (
              <button type="button" className="btn-primary btn-card" disabled={busy !== null || !canCommitThumb(cells, !!state?.chosen)} onClick={onCommit}>이걸로 정하기</button>
            )}
        </>
      )}
      {status === "awaiting_approval" && (
        <p className="text-xs text-ink-700">Higgsfield 크레딧을 쓰기 전에 사람이 정해요. <Link to="/marketing/sns?view=holds" className="text-brand-700 underline">SNS·미디어 › 사람 결정 대기</Link>에서 진행을 누르면 3장을 만들어요.</p>
      )}
      {status === "running" && <p className="animate-pulse text-xs text-ink-700">Higgsfield가 썸네일 3장을 만드는 중이에요 (보통 1~3분)</p>}
      {["failed", "blocked", "unavailable", "rejected"].includes(status) && <p className="text-xs text-accent">{thumbErrorText(state?.error) ?? "썸네일을 만들지 못했어요"}</p>}
      {status === "done" && (state?.errors?.length ?? 0) > 0 && <p className="text-xs text-ink-500">{3 - cells.length}장은 만들지 못했어요({state?.errors?.join(", ")}).</p>}
      {note && <p role={note.tone === "err" ? "alert" : "status"} className={`text-xs ${note.tone === "err" ? "text-accent" : "text-ink-700"}`}>{note.text}</p>}
    </section>
  );
}
