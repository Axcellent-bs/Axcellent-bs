import { useEffect, useState } from "react";
import { Link } from "react-router";
import { YOUTUBE_ERROR_KO, requestYoutubeUpload, saveYoutubeDraft, type YoutubePrivacy, type YoutubeState } from "@/lib/youtube";

// 제작 기록 영상 → AXcellent 유튜브 (RenderPanel 안, 2026-11-23).
// 1) AI가 제목·설명·태그 초안을 쓴다(고칠 수 있다) 2) [유튜브 올리기 요청]은 늘 사람 승인(안전 처리 로그)
// 3) 승인되면 서버가 올리고 주소를 남긴다. SNS·미디어 카드, 제작 기록 상세, AI 대화방 카드가 같이 쓴다.

const STATUS_KO: Record<string, string> = {
  draft: "초안", awaiting_approval: "사람 승인 대기", uploading: "올리는 중", done: "올림", failed: "실패", rejected: "거절됨", blocked: "막힘",
};
const PRIVACY_KO: Record<YoutubePrivacy, string> = { unlisted: "일부 공개(링크 있는 사람만)", public: "공개", private: "비공개" };
const LOCKED = new Set(["awaiting_approval", "uploading", "done"]);

export function YoutubePanel({ recordId, youtube, hasVideo, hasFinal, onChanged }: {
  recordId: string;
  youtube?: YoutubeState;
  hasVideo: boolean;
  hasFinal: boolean;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState(youtube?.title ?? "");
  const [description, setDescription] = useState(youtube?.description ?? "");
  const [privacy, setPrivacy] = useState<YoutubePrivacy>(youtube?.privacy ?? "unlisted");
  const [part, setPart] = useState<"video" | "final_video">(youtube?.part ?? (hasFinal ? "final_video" : "video"));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTitle(youtube?.title ?? "");
    setDescription(youtube?.description ?? "");
    if (youtube?.privacy) setPrivacy(youtube.privacy);
    if (youtube?.part) setPart(youtube.part);
  }, [youtube?.title, youtube?.description, youtube?.privacy, youtube?.part]);

  const status = youtube?.status ?? "";
  const locked = LOCKED.has(status);
  const anyVideo = hasVideo || hasFinal;
  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "처리하지 못했어요";
      setError(YOUTUBE_ERROR_KO[msg] ?? msg);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="space-y-2 border-t border-ink-100 pt-3" aria-label="유튜브 올리기">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-700">
        회사 유튜브
        {status && <span className="chip bg-ink-50 text-ink-700">{STATUS_KO[status] ?? status}</span>}
        {youtube?.source === "ai" && <span className="chip bg-brand-50 text-brand-700">AI 초안</span>}
      </div>
      {!anyVideo && !youtube?.title && <p className="text-xs text-ink-500">영상(Higgsfield 영상이나 직접 올린 완성 영상)이 생기면 유튜브에 올릴 수 있어요. 제목·설명 초안은 지금 만들어 둘 수 있어요.</p>}
      {status === "done" && youtube?.url ? (
        <p className="text-sm text-ink-900">유튜브에 올렸어요: <a href={youtube.url} target="_blank" rel="noreferrer" className="text-brand-700 underline">{youtube.url}</a> <span className="text-xs text-ink-500">({PRIVACY_KO[youtube.privacy ?? "unlisted"]})</span></p>
      ) : !youtube?.title ? (
        <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={() => void run("draft", () => saveYoutubeDraft(recordId, {}))}>
          {busy === "draft" ? "AI가 쓰는 중…" : "AI로 유튜브 제목·설명 쓰기"}
        </button>
      ) : (
        <div className="space-y-2">
          <input className="field text-sm" aria-label="유튜브 제목" maxLength={100} value={title} disabled={locked} onChange={(e) => setTitle(e.target.value)} />
          <textarea className="field min-h-28 text-sm" aria-label="유튜브 설명" maxLength={5000} value={description} disabled={locked} onChange={(e) => setDescription(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1">공개 범위
              <select className="field w-auto py-1 text-xs" value={privacy} disabled={locked} onChange={(e) => setPrivacy(e.target.value as YoutubePrivacy)}>
                {(Object.keys(PRIVACY_KO) as YoutubePrivacy[]).map((p) => <option key={p} value={p}>{PRIVACY_KO[p]}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1">올릴 영상
              <select className="field w-auto py-1 text-xs" value={part} disabled={locked} onChange={(e) => setPart(e.target.value as "video" | "final_video")}>
                <option value="final_video" disabled={!hasFinal}>완성 영상{hasFinal ? "" : " (없음)"}</option>
                <option value="video" disabled={!hasVideo}>Higgsfield 영상{hasVideo ? "" : " (없음)"}</option>
              </select>
            </label>
            {youtube?.tags?.length ? <span className="text-ink-500">태그 {youtube.tags.join(", ")}</span> : null}
          </div>
          {!locked && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null}
                onClick={() => void run("save", () => saveYoutubeDraft(recordId, { title, description, privacy, part }))}>{busy === "save" ? "저장 중…" : "초안 저장"}</button>
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null}
                onClick={() => void run("regen", () => saveYoutubeDraft(recordId, { regenerate: true, privacy, part }))}>{busy === "regen" ? "다시 쓰는 중…" : "AI로 다시 쓰기"}</button>
              <button type="button" className="btn-primary text-xs" disabled={busy !== null || !anyVideo || !title.trim()}
                onClick={() => void run("upload", async () => { await saveYoutubeDraft(recordId, { title, description, privacy, part }); await requestYoutubeUpload(recordId); })}>
                {busy === "upload" ? "요청 중…" : "유튜브 올리기 요청 (사람 승인)"}
              </button>
            </div>
          )}
          {status === "awaiting_approval" && <p className="text-xs text-ink-700">사람 승인이 필요해요. <Link to="/safety" className="text-brand-700 underline">안전 처리 로그</Link>의 사람 결정 대기에서 승인하면 이 제목·설명대로 올라가요.</p>}
          {status === "uploading" && <p className="animate-pulse text-xs text-ink-700">유튜브에 올리는 중이에요…</p>}
          {(status === "failed" || status === "rejected") && <p className="text-xs text-accent">{YOUTUBE_ERROR_KO[youtube?.error ?? ""] ?? youtube?.error ?? "올리지 못했어요"} · 초안을 고쳐 다시 요청할 수 있어요</p>}
        </div>
      )}
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </section>
  );
}
