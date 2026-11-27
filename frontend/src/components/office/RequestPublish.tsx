import { useCallback, useEffect, useState } from "react";
import { ChatReviewDecision } from "@/components/office/ChatReviewDecision";
import { InstagramPanel } from "@/components/marketing/InstagramPanel";
import { YoutubePanel } from "@/components/marketing/YoutubePanel";
import { listRequestVideos, requestVideoCoverUrl } from "@/lib/requestVideos";
import { APPROVAL_COVER_KO, PUBLISH_CHANNELS, approvalCoverId, canUploadToYoutube, publishStatusKo, type PublishChannel, type RequestVideo } from "@/lib/requestVideoView";
import { useAuthedMedia } from "@/lib/authedMedia";

// 대화방 의뢰 카드 › 영상 제작 단계 아래 "어디에 올릴까요?" (11/26). 제작 기록에 있던 유튜브·인스타 칸을 대화방 안에서 그대로 연다.
// - 의뢰로 만든 영상(AI 표시 입힌 것)이 있을 때만 보인다. 없으면 아무것도 그리지 않는다
// - [유튜브] [인스타그램 (릴스)]: 누르면 그 칸이 카드 안에 펼쳐진다(AI 초안 → 올리기 요청). 둘 다 해도 된다
// - 올리기는 늘 사람 승인(안전 처리 로그, 요청한 계정과 다른 계정). 승인 대기면 이 카드 안에서 바로 승인·거절한다(11/26)

/** 11/27 C 승인 카드 위 썸네일: 승인하면 올라갈 그림(AI 표시가 들어간 프레임). 사람 토큰이 필요해 useAuthedMedia로 읽는다.
 *  썸네일이 없거나 못 받으면 그리지 않는다(승인 카드는 그대로) */
function ApprovalCover({ postDraftId, mediaId, what }: { postDraftId: string; mediaId: string | null; what: string }) {
  const { src } = useAuthedMedia(mediaId ? requestVideoCoverUrl(postDraftId, mediaId) : null);
  if (!src) return null;
  return (
    <figure className="flex min-w-0 items-center gap-2 text-xs text-ink-700" aria-label={`${what} (승인하면 올라갈 그림)`}>
      <img src={src} alt={`${what} (AI 생성 영상에서 뽑은 그림)`} className="h-16 w-auto shrink-0 rounded border border-ink-100 object-cover" />
      <figcaption className="min-w-0 break-words">{what} · {APPROVAL_COVER_KO}</figcaption>
    </figure>
  );
}

export function RequestPublish({ requestId }: { requestId: string }) {
  const [video, setVideo] = useState<RequestVideo | null>(null);
  const [open, setOpen] = useState<PublishChannel | null>(null);
  const load = useCallback(() => {
    listRequestVideos(requestId, 1).then((items) => setVideo(items[0] ?? null)).catch(() => setVideo(null));
  }, [requestId]);
  useEffect(load, [load]);
  if (!video || !canUploadToYoutube(video)) return null;
  const cover = video.cover_media_id
    ? { url: requestVideoCoverUrl(video.post_draft_id, video.cover_media_id), picked: video.cover_picked === true }
    : null;
  return (
    <section aria-label="영상 올리기" className="mt-2 space-y-1.5">
      <p className="text-xs font-medium text-ink-700">어디에 올릴까요?</p>
      <span className="flex flex-wrap gap-1.5">
        {PUBLISH_CHANNELS.map((c) => {
          const status = publishStatusKo(video, c.key);
          return (
            <button key={c.key} type="button" aria-expanded={open === c.key}
              className={`btn-card ${open === c.key ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setOpen((cur) => (cur === c.key ? null : c.key))}>
              {c.label}{status ? ` · ${status}` : ""}
            </button>
          );
        })}
      </span>
      {video.youtube?.status === "awaiting_approval" && video.youtube.review_id && (
        <>
          <ApprovalCover postDraftId={video.post_draft_id} mediaId={approvalCoverId(video, video.youtube)} what="유튜브 썸네일" />
          <ChatReviewDecision reviewId={video.youtube.review_id} what="유튜브 올리기" onDone={load} />
        </>
      )}
      {video.instagram?.status === "awaiting_approval" && video.instagram.review_id && (
        <>
          <ApprovalCover postDraftId={video.post_draft_id} mediaId={approvalCoverId(video, video.instagram)} what="릴스 표지" />
          <ChatReviewDecision reviewId={video.instagram.review_id} what="인스타그램 올리기" onDone={load} />
        </>
      )}
      {open === "youtube" && <YoutubePanel requestVideoId={video.post_draft_id} youtube={video.youtube} onChanged={load} />}
      {open === "instagram" && <InstagramPanel requestVideoId={video.post_draft_id} instagram={video.instagram} cover={cover} onChanged={load} />}
    </section>
  );
}
