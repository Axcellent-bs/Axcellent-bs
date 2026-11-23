import { useCallback, useEffect, useState } from "react";
import { useAuthedMedia } from "@/lib/authedMedia";
import { listRequestVideos, requestVideoCoverUrl, selectRequestVideoCover } from "@/lib/requestVideos";
import { COVER_CONFIRM_KO, canCommitCover, coverCells, needsCoverConfirm, type CoverCell, type RequestVideo } from "@/lib/requestVideoView";

// 의뢰 영상 썸네일 후보 3장 → 사람이 고르기 (시안 ③). 제작 기록 › 의뢰로 만든 영상 카드와 대화방 의뢰 카드(영상 제작 완료)에 둔다.
// 후보는 AI 라벨을 입힌 영상에서 뽑은 그림이라 AI 표시가 들어 있다. 고르지 않으면 첫 후보가 기본값이고 게시·유튜브 승인에 같이 들어간다.
// 승인 대기·승인 뒤에 바꾸면 그 승인이 풀려 다시 받아야 한다(바꾸기 전에 확인 상자).

function Thumb({ postDraftId, cell, onPick }: { postDraftId: string; cell: CoverCell; onPick: () => void }) {
  const { src, failed } = useAuthedMedia(requestVideoCoverUrl(postDraftId, cell.media_id));
  const ring = cell.selected ? "border-accent ring-2 ring-accent/40" : "border-ink-100";
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        aria-pressed={cell.selected}
        aria-label={`썸네일 후보 ${cell.at || ""}${cell.chosen ? " (정해진 썸네일)" : ""}`}
        className={`relative block w-24 overflow-hidden rounded-lg border-2 bg-ink-50 ${ring}`}
      >
        {src ? <img src={src} alt="" className="aspect-[9/16] w-full object-cover" />
          : <span className="grid aspect-[9/16] w-full place-items-center text-[11px] text-ink-500">{failed ? "못 불러옴" : "불러오는 중…"}</span>}
        {cell.chosen && (
          <span className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-white" aria-hidden="true">✓</span>
        )}
        {cell.at && <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] tabular-nums text-white">{cell.at}</span>}
      </button>
    </li>
  );
}

export function CoverPicker({ video, onChanged }: { video: RequestVideo; onChanged: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const cells = coverCells(video, selected);
  if (cells.length === 0) return null;
  const canCommit = canCommitCover(cells, video.cover_picked === true);
  const target = cells.find((c) => c.selected);

  const commit = () => {
    if (!target) return;
    setBusy(true);
    setNote(null);
    selectRequestVideoCover(video.post_draft_id, target.media_id)
      .then((r) => {
        setConfirming(false);
        setSelected(null);
        setNote({ tone: "ok", text: r.approvals_reset.length ? "썸네일을 바꿨어요. 게시 승인을 다시 요청해 주세요." : "썸네일을 정했어요." });
        onChanged();
      })
      .catch((e: unknown) => setNote({ tone: "err", text: e instanceof Error ? e.message : "썸네일을 정하지 못했어요" }))
      .finally(() => setBusy(false));
  };
  const onCommit = () => (needsCoverConfirm(cells, video) ? setConfirming(true) : commit());

  return (
    <div className="space-y-2" aria-label="썸네일 고르기">
      <p className="text-xs font-semibold text-ink-700">썸네일 <span className="font-normal text-ink-500">· 영상에서 뽑은 3장(AI 표시 포함). 고르지 않으면 첫 장이 쓰여요</span></p>
      <ul className="flex flex-wrap gap-2">
        {cells.map((c) => <Thumb key={c.media_id} postDraftId={video.post_draft_id} cell={c} onPick={() => { setSelected(c.media_id); setConfirming(false); }} />)}
      </ul>
      {confirming ? (
        <div role="alertdialog" aria-label="썸네일 바꾸기 확인" className="space-y-2 rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <p>{COVER_CONFIRM_KO}</p>
          <span className="flex flex-wrap gap-1.5">
            <button type="button" className="btn-primary btn-card" disabled={busy} onClick={commit}>바꾸기</button>
            <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => setConfirming(false)}>그대로 두기</button>
          </span>
        </div>
      ) : (
        <button type="button" className="btn-primary btn-card" disabled={busy || !canCommit} onClick={onCommit}>이걸로 정하기</button>
      )}
      {note && <p role={note.tone === "err" ? "alert" : "status"} className={`text-xs ${note.tone === "err" ? "text-red-700" : "text-ink-700"}`}>{note.text}</p>}
    </div>
  );
}

/** 대화방 의뢰 카드용: 그 의뢰의 가장 최근 영상 썸네일 고르기. 영상·후보가 없으면 그리지 않는다 */
export function RequestCoverCell({ requestId }: { requestId: string }) {
  const [video, setVideo] = useState<RequestVideo | null>(null);
  const load = useCallback(() => {
    listRequestVideos(requestId, 1).then((items) => setVideo(items[0] ?? null)).catch(() => setVideo(null));
  }, [requestId]);
  useEffect(load, [load]);
  if (!video || !video.labeled) return null;
  return <div className="mt-2"><CoverPicker video={video} onChanged={load} /></div>;
}
