import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Skeleton } from "@/components/finance/bits";
import { useAuthedMedia } from "@/lib/authedMedia";
import { shortDateTime } from "@/lib/format";
import { listRequestVideos, requestVideoPreviewUrl } from "@/lib/requestVideos";
import { AI_LABEL_NOTE_KO, VIDEOS_EMPTY_KO, durationKo, requestFilterOf, sortForRequest, statusKo, videoPane, type RequestVideo } from "@/lib/requestVideoView";

// SNS·미디어 › 의뢰로 만든 영상. 총괄 홍보 영상 의뢰(wr_…)가 만든 reel 초안 중 AI 라벨을 입힌 영상을 본다.
// 읽기만 한다. 게시·승인은 기획안 카드의 승인 패널에서 한다. ?request=wr_… 이면 그 의뢰 영상을 맨 앞에 두고 강조한다.

function Player({ v }: { v: RequestVideo }) {
  const url = requestVideoPreviewUrl(v.post_draft_id);
  const { src, failed } = useAuthedMedia(url);
  if (failed || !url) return <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">영상을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>;
  if (!src) return <p className="text-xs text-ink-500">영상 불러오는 중…</p>;
  return <video className="max-h-96 rounded-xl bg-black" src={src} controls preload="metadata" aria-label="AI 표시가 들어간 영상" />;
}

function Item({ v, focus }: { v: RequestVideo; focus: boolean }) {
  const pane = videoPane(v);
  return (
    <li className={`space-y-2 px-4 py-3 text-xs ${focus ? "bg-brand-50" : ""}`} aria-current={focus ? "true" : undefined}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-ink-700">{v.request_id}</span>
        <span className="chip bg-ink-50 text-ink-700">{statusKo(v.status)}</span>
        <span className="tabular-nums text-ink-500">{durationKo(v.duration_s)}</span>
        <span className="ml-auto text-ink-500">{shortDateTime(v.created_at)}</span>
      </div>
      {v.caption_ko && <p className="whitespace-pre-line break-words text-sm text-ink-900">{v.caption_ko}</p>}
      {pane.kind === "play" ? <Player v={v} /> : <p className="rounded-xl bg-ink-50 px-3 py-2 text-ink-700">{pane.text}</p>}
    </li>
  );
}

export function RequestVideos() {
  const [params] = useSearchParams();
  const requestId = requestFilterOf(params.get("request"));
  const [items, setItems] = useState<RequestVideo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setItems(null);
    setError(null);
    listRequestVideos(requestId).then(
      (x) => live && setItems(x),
      (e: unknown) => live && setError(e instanceof Error ? e.message : "의뢰로 만든 영상을 읽지 못했어요"),
    );
    return () => { live = false; };
  }, [requestId]);

  return (
    <section id="request-videos" className="card space-y-4 p-6" aria-labelledby="request-videos-h">
      <div>
        <h2 id="request-videos-h" className="text-lg font-semibold">의뢰로 만든 영상</h2>
        <p className="mt-1 text-xs text-ink-500">총괄에 맡긴 홍보 영상 의뢰가 만든 SNS 영상 초안입니다. AI 표시가 입혀진 영상만 재생하고, 게시는 하지 않습니다. {AI_LABEL_NOTE_KO}</p>
        {requestId && (
          <p className="mt-1 text-xs text-ink-700">의뢰 <span className="font-mono">{requestId}</span>의 영상을 보고 있어요. <Link to="/marketing/sns" className="underline">전체 보기</Link></p>
        )}
      </div>
      {error ? <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>
        : items === null ? <Skeleton stats={0} lines={2} />
          : items.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">{requestId ? "이 의뢰로 만든 영상이 아직 없어요." : VIDEOS_EMPTY_KO}</p> : (
            <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100" aria-label="의뢰로 만든 영상 목록">
              {sortForRequest(items, requestId).map((v) => <Item key={v.post_draft_id} v={v} focus={!!requestId && v.request_id === requestId} />)}
            </ul>
          )}
    </section>
  );
}
