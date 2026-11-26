import { useAuthedMedia } from "@/lib/authedMedia";
import { shortDateTime } from "@/lib/format";
import { requestVideoCoverUrl, requestVideoPreviewUrl } from "@/lib/requestVideos";
import { AI_LABEL_NOTE_KO, canUploadToYoutube, durationKo, statusKo, videoPane, type RequestVideo } from "@/lib/requestVideoView";
import { CoverPicker } from "./CoverPicker";
import { InstagramPanel } from "./InstagramPanel";
import { YoutubePanel } from "./YoutubePanel";

// 총괄 홍보 영상 의뢰(wr_…)가 만든 영상의 칸(제작 기록 상세의 '영상'·'올리기' 단계).
// AI 표시를 입힌 영상만 재생·올린다. 영상마다 썸네일 후보 3장에서 하나를 고르고, 올리기는 늘 사람 승인(C)이다.
// 기획안 Instagram 게시·승인은 기획안 카드의 승인 패널에서 한다.

function Player({ v }: { v: RequestVideo }) {
  const url = requestVideoPreviewUrl(v.post_draft_id);
  const { src, failed } = useAuthedMedia(url);
  if (failed || !url) return <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">영상을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>;
  if (!src) return <p className="text-xs text-ink-500">영상 불러오는 중…</p>;
  return <video className="w-full max-w-sm rounded-xl bg-black" src={src} controls preload="metadata" aria-label="AI 표시가 들어간 영상" />;
}

/** 영상 단계: 의뢰에 낸 영상(AI 표시 포함)과 썸네일 고르기 */
export function RequestVideoCard({ v, onChanged }: { v: RequestVideo; onChanged: () => void }) {
  const pane = videoPane(v);
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-ink-900">의뢰에 낸 영상</span>
        <span className="chip bg-ink-50 text-ink-700">{statusKo(v.status)}</span>
        <span className="tabular-nums text-ink-500">{durationKo(v.duration_s)}</span>
        <span className="font-mono text-ink-500">{v.request_id}</span>
        <span className="text-ink-500">{shortDateTime(v.created_at)}</span>
      </div>
      <p className="text-ink-500">{AI_LABEL_NOTE_KO}</p>
      {pane.kind === "play" ? <Player v={v} /> : <p className="rounded-xl bg-ink-50 px-3 py-2 text-ink-700">{pane.text}</p>}
      {pane.kind === "play" && <CoverPicker video={v} onChanged={onChanged} />}
      {v.caption_ko && (
        <details>
          <summary className="cursor-pointer text-ink-500">게시 글</summary>
          <p className="whitespace-pre-line break-words pt-2 text-sm text-ink-900">{v.caption_ko}</p>
        </details>
      )}
    </div>
  );
}

/** 올리기 단계: 의뢰 영상 한 건의 유튜브·인스타 */
export function RequestVideoPublish({ v, onChanged }: { v: RequestVideo; onChanged: () => void }) {
  if (!canUploadToYoutube(v)) return <p className="text-xs text-ink-500">AI 표시가 입혀진 뒤에 회사 유튜브·인스타그램에 올릴 수 있어요.</p>;
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold text-ink-900">의뢰 영상(AI 표시 포함) <span className="font-mono font-normal text-ink-500">{v.request_id}</span></p>
      <YoutubePanel requestVideoId={v.post_draft_id} youtube={v.youtube} onChanged={onChanged} />
      <InstagramPanel requestVideoId={v.post_draft_id} instagram={v.instagram} onChanged={onChanged}
        cover={v.cover_media_id ? { url: requestVideoCoverUrl(v.post_draft_id, v.cover_media_id), picked: v.cover_picked === true } : null} />
    </div>
  );
}
