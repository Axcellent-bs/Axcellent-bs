import { useCallback, useEffect, useState } from "react";
import { InstagramPanel } from "@/components/marketing/InstagramPanel";
import { YoutubePanel } from "@/components/marketing/YoutubePanel";
import { listRequestVideos, requestVideoCoverUrl } from "@/lib/requestVideos";
import { PUBLISH_CHANNELS, canUploadToYoutube, publishStatusKo, type PublishChannel, type RequestVideo } from "@/lib/requestVideoView";

// 대화방 의뢰 카드 › 영상 제작 단계 아래 "어디에 올릴까요?" (11/26). 제작 기록에 있던 유튜브·인스타 칸을 대화방 안에서 그대로 연다.
// - 의뢰로 만든 영상(AI 표시 입힌 것)이 있을 때만 보인다. 없으면 아무것도 그리지 않는다
// - [유튜브] [인스타그램 (릴스)]: 누르면 그 칸이 카드 안에 펼쳐진다(AI 초안 → 올리기 요청). 둘 다 해도 된다
// - 올리기는 늘 사람 승인(안전 처리 로그, 요청한 계정과 다른 계정). 여기서 바로 올리지 않는다

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
      {open === "youtube" && <YoutubePanel requestVideoId={video.post_draft_id} youtube={video.youtube} onChanged={load} />}
      {open === "instagram" && <InstagramPanel requestVideoId={video.post_draft_id} instagram={video.instagram} cover={cover} onChanged={load} />}
    </section>
  );
}
