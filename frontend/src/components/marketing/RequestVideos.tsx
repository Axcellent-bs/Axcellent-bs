import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Skeleton } from "@/components/finance/bits";
import { useAuthedMedia } from "@/lib/authedMedia";
import { shortDateTime } from "@/lib/format";
import { listRequestVideos, requestVideoPreviewUrl } from "@/lib/requestVideos";
import { AI_LABEL_NOTE_KO, REQUEST_VIDEOS_PATH, VIDEOS_EMPTY_KO, VIDEO_NOT_FOUND_KO, canUploadToYoutube, durationKo, focusDraftId, requestFilterOf, sortForRequest, statusKo, videoPane, type RequestVideo } from "@/lib/requestVideoView";
import { CoverPicker } from "./CoverPicker";
import { InstagramPanel } from "./InstagramPanel";
import { YoutubePanel } from "./YoutubePanel";

// 제작 기록 › 의뢰로 만든 영상 (/marketing/records?view=videos). 총괄 홍보 영상 의뢰(wr_…)가 만든 reel 초안 중
// AI 라벨을 입힌 영상을 본다. 영상마다 썸네일 후보 3장에서 하나를 고르고(시안 ③), 회사 유튜브에 올릴 수 있다
// (AI 제목·설명 초안 → 사람 승인 C → 올리기). 11/23: 회사 인스타그램 릴스 칸도 같은 방식
// (AI 캡션 초안 → 사람 승인 C publish_instagram_video → 게시물 주소). 기획안 Instagram 게시·승인은 기획안 카드의 승인 패널에서 한다.
// ?request=wr_… 이면 그 의뢰 영상을 맨 앞에 두고, 그 카드로 한 번 스크롤해 테두리(accent)로 표시한다.

function Player({ v }: { v: RequestVideo }) {
  const url = requestVideoPreviewUrl(v.post_draft_id);
  const { src, failed } = useAuthedMedia(url);
  if (failed || !url) return <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">영상을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>;
  if (!src) return <p className="text-xs text-ink-500">영상 불러오는 중…</p>;
  return <video className="max-h-96 rounded-xl bg-black" src={src} controls preload="metadata" aria-label="AI 표시가 들어간 영상" />;
}

function Item({ v, focus, onChanged }: { v: RequestVideo; focus: boolean; onChanged: () => void }) {
  const pane = videoPane(v);
  const ref = useRef<HTMLLIElement>(null);
  const scrolled = useRef(false);
  useEffect(() => {
    if (!focus || scrolled.current) return; // 들어왔을 때 한 번만
    scrolled.current = true;
    ref.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [focus]);
  return (
    <li
      ref={ref}
      className={`space-y-2 px-4 py-3 text-xs ${focus ? "rounded-2xl border-2 border-accent bg-accent-50" : ""}`}
      aria-current={focus ? "true" : undefined}
      data-focus={focus ? "request" : undefined}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-ink-700">{v.request_id}</span>
        <span className="chip bg-ink-50 text-ink-700">{statusKo(v.status)}</span>
        <span className="tabular-nums text-ink-500">{durationKo(v.duration_s)}</span>
        <span className="ml-auto text-ink-500">{shortDateTime(v.created_at)}</span>
      </div>
      {v.caption_ko && <p className="whitespace-pre-line break-words text-sm text-ink-900">{v.caption_ko}</p>}
      {pane.kind === "play" ? <Player v={v} /> : <p className="rounded-xl bg-ink-50 px-3 py-2 text-ink-700">{pane.text}</p>}
      {pane.kind === "play" && <CoverPicker video={v} onChanged={onChanged} />}
      {canUploadToYoutube(v)
        ? <>
          <YoutubePanel requestVideoId={v.post_draft_id} youtube={v.youtube} onChanged={onChanged} />
          <InstagramPanel requestVideoId={v.post_draft_id} instagram={v.instagram} onChanged={onChanged} />
        </>
        : <p className="text-ink-500">AI 표시가 입혀진 뒤에 회사 유튜브·인스타그램에 올릴 수 있어요.</p>}
    </li>
  );
}

export function RequestVideos() {
  const [params] = useSearchParams();
  const requestId = requestFilterOf(params.get("request"));
  const [items, setItems] = useState<RequestVideo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setItems(await listRequestVideos(requestId));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "의뢰로 만든 영상을 읽지 못했어요");
    }
  }, [requestId]);
  useEffect(() => {
    setItems(null);
    void load();
  }, [load]);
  const focusId = items ? focusDraftId(items, requestId) : null;

  return (
    <section id="request-videos" className="space-y-4" aria-labelledby="request-videos-h">
      <div>
        <h2 id="request-videos-h" className="text-lg font-semibold">의뢰로 만든 영상</h2>
        <p className="mt-1 text-xs text-ink-500">총괄에 맡긴 홍보 영상 의뢰가 만든 SNS 영상 초안입니다. AI 표시가 입혀진 영상만 재생하고, 회사 유튜브에는 사람 승인을 거쳐 그 영상 그대로 올라갑니다. {AI_LABEL_NOTE_KO}</p>
        {requestId && (
          <p className="mt-1 text-xs text-ink-700">의뢰 <span className="font-mono">{requestId}</span>의 영상을 보고 있어요. <Link to={REQUEST_VIDEOS_PATH} className="underline">전체 보기</Link></p>
        )}
      </div>
      {error ? <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>
        : items === null ? <Skeleton stats={0} lines={2} />
          : items.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">{requestId ? VIDEO_NOT_FOUND_KO : VIDEOS_EMPTY_KO}</p> : (
            <>
              {requestId && !focusId && <p role="status" className="rounded-xl bg-ink-50 px-4 py-2 text-xs text-ink-700">{VIDEO_NOT_FOUND_KO}</p>}
              <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100" aria-label="의뢰로 만든 영상 목록">
                {sortForRequest(items, requestId).map((v) => <Item key={v.post_draft_id} v={v} focus={v.post_draft_id === focusId} onChanged={() => void load()} />)}
              </ul>
            </>
          )}
    </section>
  );
}
