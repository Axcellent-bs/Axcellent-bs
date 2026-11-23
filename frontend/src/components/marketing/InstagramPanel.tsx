import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuthedMedia } from "@/lib/authedMedia";
import { parseHashtags } from "@/lib/instagramCaption";
import {
  INSTAGRAM_STATUS_KO, instagramErrorKo, requestInstagramPost, requestRequestVideoInstagramPost, safePermalink,
  saveInstagramDraft, saveRequestVideoInstagramDraft, type InstagramDraftEdits, type InstagramPostState,
} from "@/lib/instagramPost";

// 제작 기록 영상·의뢰로 만든 영상 → 회사 인스타그램 릴스 (유튜브 칸 옆, 2026-11-23).
// 1) AI가 캡션·해시태그 초안을 쓴다(고칠 수 있다, 캡션 끝 AI 생성 문구는 서버가 늘 붙인다)
// 2) [인스타에 올리기 요청 (사람 승인)]은 늘 사람 승인(안전 처리 로그, SNS·미디어 › 사람 결정 대기)
// 3) 승인되면 서버가 AI 표시를 입힌 영상만 회사 연결 계정에 릴스로 올리고 게시물 주소를 남긴다

const LOCKED = new Set(["awaiting_approval", "posting", "done"]);

type Target =
  | { recordId: string; hasVideo: boolean; hasFinal: boolean; requestVideoId?: undefined }
  | { requestVideoId: string; recordId?: undefined; hasVideo?: undefined; hasFinal?: undefined };

function api(t: Target) {
  if (t.requestVideoId !== undefined) {
    const id = t.requestVideoId;
    return { save: (e: InstagramDraftEdits) => saveRequestVideoInstagramDraft(id, e), request: () => requestRequestVideoInstagramPost(id) };
  }
  const id = t.recordId;
  return { save: (e: InstagramDraftEdits) => saveInstagramDraft(id, e), request: () => requestInstagramPost(id) };
}

/** 릴스 표지: 고른 썸네일(안 골랐으면 첫 장). 주소는 사람 토큰이 필요해 useAuthedMedia로 읽는다 */
export interface ReelCover { url: string | null; picked: boolean }

function CoverLine({ cover }: { cover: ReelCover }) {
  const { src, failed } = useAuthedMedia(cover.url);
  return (
    <div className="flex items-center gap-2 text-xs text-ink-700" aria-label="릴스 표지">
      {src ? <img src={src} alt="릴스 표지로 쓸 썸네일" className="h-12 w-auto rounded border border-ink-100 object-cover" />
        : <span className="grid h-12 w-8 place-items-center rounded border border-ink-100 bg-ink-50 text-[10px] text-ink-500">{failed ? "없음" : "…"}</span>}
      <span>표지: 고른 썸네일{cover.picked ? "" : " (안 골라서 첫 장)"} · 썸네일을 바꾸면 승인을 다시 받아요</span>
    </div>
  );
}

export function InstagramPanel(props: Target & { instagram?: InstagramPostState | null; cover?: ReelCover | null; onChanged: () => void }) {
  const { instagram, onChanged, cover } = props;
  const forRequest = props.requestVideoId !== undefined;
  const hasVideo = forRequest || !!props.hasVideo;
  const hasFinal = !forRequest && !!props.hasFinal;
  const { save, request } = api(props);
  const [caption, setCaption] = useState(instagram?.caption ?? "");
  const [tags, setTags] = useState((instagram?.hashtags ?? []).join(" "));
  const [part, setPart] = useState<"video" | "final_video">(instagram?.part === "video" || instagram?.part === "final_video" ? instagram.part : hasFinal ? "final_video" : "video");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCaption(instagram?.caption ?? "");
    setTags((instagram?.hashtags ?? []).join(" "));
    if (instagram?.part === "video" || instagram?.part === "final_video") setPart(instagram.part);
  }, [instagram?.caption, instagram?.hashtags, instagram?.part]);

  const status = instagram?.status ?? "";
  const locked = LOCKED.has(status);
  const anyVideo = hasVideo || hasFinal;
  const link = safePermalink(instagram?.permalink);
  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(instagramErrorKo(e instanceof Error ? e.message : null));
    } finally {
      setBusy(null);
    }
  };
  const edits = (): InstagramDraftEdits => ({ caption, hashtags: parseHashtags(tags), ...(forRequest ? {} : { part }) });

  return (
    <section className="space-y-2 border-t border-ink-100 pt-3" aria-label="인스타그램 올리기">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-700">
        회사 인스타그램 (릴스)
        {status && <span className="chip bg-ink-50 text-ink-700">{INSTAGRAM_STATUS_KO[status] ?? status}</span>}
        {instagram?.source === "ai" && <span className="chip bg-brand-50 text-brand-700">AI 초안</span>}
      </div>
      {!anyVideo && !instagram?.caption && <p className="text-xs text-ink-500">영상이 생기면 인스타그램에 릴스로 올릴 수 있어요. 캡션 초안은 지금 만들어 둘 수 있어요.</p>}
      {status === "done" ? (
        <p className="text-sm text-ink-900">
          인스타그램에 올렸어요{link ? <>: <a href={link} target="_blank" rel="noreferrer" className="text-brand-700 underline">{link}</a></> : " (게시물 주소는 인스타 앱에서 확인해 주세요)"}
        </p>
      ) : !instagram?.caption ? (
        <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={() => void run("draft", () => save(forRequest ? {} : { part }))}>
          {busy === "draft" ? "AI가 쓰는 중…" : "AI로 인스타 캡션·해시태그 쓰기"}
        </button>
      ) : (
        <div className="space-y-2">
          <textarea className="field min-h-28 text-sm" aria-label="인스타 캡션" maxLength={2200} value={caption} disabled={locked} onChange={(e) => setCaption(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex flex-1 items-center gap-1">해시태그
              <input className="field py-1 text-xs" aria-label="인스타 해시태그" placeholder="AI직원 업무자동화 (최대 5개, #AXcellent는 늘 붙어요)" value={tags} disabled={locked} onChange={(e) => setTags(e.target.value)} />
            </label>
            {forRequest ? <span className="chip bg-ink-50 text-ink-700">올릴 영상: AI 표시가 들어간 의뢰 영상</span> : (
              <label className="flex items-center gap-1">올릴 영상
                <select className="field w-auto py-1 text-xs" value={part} disabled={locked} onChange={(e) => setPart(e.target.value as "video" | "final_video")}>
                  <option value="final_video" disabled={!hasFinal}>완성 영상{hasFinal ? "" : " (없음)"}</option>
                  <option value="video" disabled={!hasVideo}>Higgsfield 영상{hasVideo ? "" : " (없음)"}</option>
                </select>
              </label>
            )}
          </div>
          {cover?.url && <CoverLine cover={cover} />}
          {!locked && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={() => void run("save", () => save(edits()))}>{busy === "save" ? "저장 중…" : "초안 저장"}</button>
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null}
                onClick={() => void run("regen", () => save({ regenerate: true, ...(forRequest ? {} : { part }) }))}>{busy === "regen" ? "다시 쓰는 중…" : "AI로 다시 쓰기"}</button>
              <button type="button" className="btn-primary text-xs" disabled={busy !== null || !anyVideo || !caption.trim()}
                onClick={() => void run("post", async () => { await save(edits()); await request(); })}>
                {busy === "post" ? (forRequest ? "요청 중…" : "AI 표시 입히고 요청 중…") : "인스타에 올리기 요청 (사람 승인)"}
              </button>
            </div>
          )}
          <p className="text-xs text-ink-500">영상에 AI 표시를 입혀 올리고, 인스타에도 AI 생성 표시를 켜요. 캡션 끝 AI 생성 문구는 지울 수 없어요.</p>
          {status === "awaiting_approval" && <p className="text-xs text-ink-700">사람 승인이 필요해요. <Link to="/marketing/sns?view=holds" className="text-brand-700 underline">SNS·미디어 › 사람 결정 대기</Link>에서 진행을 누르면 이 캡션대로 올라가요(요청한 계정과 다른 계정으로).</p>}
          {status === "posting" && <p className="animate-pulse text-xs text-ink-700">인스타그램에 올리는 중이에요…</p>}
          {(status === "failed" || status === "rejected") && <p className="text-xs text-accent">{instagramErrorKo(instagram?.error)} · 캡션을 고쳐 다시 요청할 수 있어요</p>}
        </div>
      )}
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </section>
  );
}
