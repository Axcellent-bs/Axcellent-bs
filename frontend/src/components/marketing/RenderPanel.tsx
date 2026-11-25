import { useCallback, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { useAuthedMedia } from "@/lib/authedMedia";
import { renderErrorText } from "@/lib/creativeCardView";
import { InstagramPanel } from "./InstagramPanel";
import { HIGGSFIELD_URL, acceptFor, isVideoPart, pickDropped, storyboardVideoPrompt, uploadProblem } from "@/lib/higgsfieldWeb";
import { ThumbnailPicker } from "./ThumbnailPicker";
import { chosenThumb, thumbUrl } from "@/lib/creativeThumbnails";
import { YoutubePanel } from "./YoutubePanel";
import type { StoryboardResult } from "@/lib/storyboard";
import { canToggleOriginal, overlayVisibleFor, playPartFor, type PlayPart } from "@/lib/videoOverlay";
import { OverlayControls } from "./OverlayControls";
import { KeyframePairs } from "./KeyframePairs";
import { ApprovalDeadline, TimedOutRetry } from "./ApprovalDeadline";
import { timedOut } from "@/lib/videoApprovalTime";
import { framesOf, keyframesDone } from "@/lib/keyframes";
import { videoSummaryLine } from "@/lib/creativeVideo";
import { VideoQc } from "@/components/marketing/VideoQc";
import { canAskVideo, usesOriginalReference } from "@/lib/videoAsk";
import { assetUrl, getCreativeRecord, requestCreativeVideo, uploadCreativeFile, type CreativeRecord, type RenderPart, type RenderPartKey } from "@/lib/creativeRecords";

// 제작 기록 한 건의 Higgsfield 결과: 이미지 시트 그림 · 스토리보드 그림 · 스토리보드 영상(사람 승인 뒤).
// 다른 도구로 만든 결과(그림·완성 영상)는 칸마다 [파일 올리기]로 직접 붙인다.
// SNS·미디어 카드, 제작 기록 상세, AI 대화방 카드가 같이 쓴다(영상이 생기면 썸네일 3장 고르기 칸 `ThumbnailPicker`도 여기 있다). 만드는 중·승인 대기면 5초마다 다시 읽는다
// (기록을 읽을 때 마케팅이 영상 승인 여부를 다시 확인해 승인됐으면 만들기 시작한다).

const POLL_MS = 5_000;
const PENDING = new Set(["running", "awaiting_approval", "uploading"]);
const STATUS_KO: Record<string, string> = {
  running: "만드는 중", done: "완료", failed: "실패", blocked: "막힘", unavailable: "설정 없음",
  awaiting_approval: "사람 승인 대기", rejected: "거절됨",
};
// 오류 코드 말은 대화방 카드(CreativeChatCard)와 같이 쓴다 (lib/creativeCardView.ts)
const errorText = renderErrorText;

function Status({ part }: { part?: RenderPart | null }) {
  const s = part?.status || "";
  if (!s) return <span className="chip bg-ink-50 text-ink-500">대기</span>;
  const tone = s === "done" ? "bg-emerald-50 text-emerald-800" : PENDING.has(s) ? "bg-brand-50 text-brand-700" : s === "unavailable" ? "bg-ink-50 text-ink-500" : "bg-amber-50 text-amber-800";
  return <span className={`chip ${tone}`}>{STATUS_KO[s] ?? s}</span>;
}

const IS_VIDEO = isVideoPart;

/** 칸에 파일 올리기. [파일 올리기]와 끌어 놓기가 같은 검사(uploadProblem)·같은 올리기 함수를 쓴다 */
function useUpload(recordId: string, part: RenderPartKey, onDone: () => void, onError: (m: string) => void) {
  const [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    const problem = uploadProblem(part, file);
    if (problem) { onError(problem); return; }
    setBusy(true);
    try {
      await uploadCreativeFile(recordId, part, file);
      onDone();
    } catch (e) {
      onError(e instanceof Error ? e.message : "파일을 올리지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  return { busy, upload };
}

function UploadButton({ part, busy, onPick }: { part: RenderPartKey; busy: boolean; onPick: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={input} type="file" className="hidden" accept={acceptFor(part)}
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onPick(f); }} />
      <button type="button" className="btn-ghost ml-auto text-xs" disabled={busy} onClick={() => input.current?.click()}>{busy ? "올리는 중…" : "파일 올리기"}</button>
    </>
  );
}

/** 영상 칸의 끌어 놓기 자리. 끌고 오는 동안 브랜드 색 점선으로 바뀐다 */
function DropArea({ enabled, busy, onFile, onError, children }: { enabled: boolean; busy: boolean; onFile: (file: File) => void; onError: (m: string) => void; children: ReactNode }) {
  const [over, setOver] = useState(false);
  if (!enabled) return <>{children}</>;
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    if (busy) return;
    const { file, problem } = pickDropped(Array.from(e.dataTransfer.files));
    if (problem) onError(problem);
    else if (file) onFile(file as File);
  };
  return (
    <div
      className={`rounded-xl border-2 border-dashed p-1 transition-colors ${over ? "border-brand-500 bg-brand-50" : "border-ink-300"}`}
      onDragEnter={(e) => { e.preventDefault(); setOver(true); }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = busy ? "none" : "copy"; }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false); }}
      onDrop={onDrop}
    >
      {children}
      <p className="px-2 py-1 text-center text-xs text-ink-500">{busy ? "올리는 중…" : over ? "놓으면 올려요" : "MP4 파일을 여기로 끌어 놓아도 올라가요"}</p>
    </div>
  );
}

function Picture({ recordId, part, info, label, onChanged, onError, playPart, version }: { recordId: string; part: RenderPartKey; info?: RenderPart | null; label: string; onChanged: () => void; onError: (m: string) => void; playPart?: PlayPart; version?: string }) {
  const done = info?.status === "done" && !!info.sha256;
  const uploaded = info?.source === "upload";
  const busy = info?.status === "running" || info?.status === "awaiting_approval";
  // 자막을 얹은 파일(overlay_video)을 재생할 때는 그 파일 주소를 쓴다(올리기·끌어 놓기는 늘 원래 칸 part)
  const { src, failed } = useAuthedMedia(done ? assetUrl(recordId, playPart ?? part, playPart === "overlay_video" && version ? version : info?.sha256 ?? null) : null);
  const { busy: uploading, upload } = useUpload(recordId, part, onChanged, onError);
  return (
    <figure className="space-y-1">
      <figcaption className="flex items-center gap-2 text-xs font-medium text-ink-700">
        {label} <Status part={info} />
        {uploaded && <span className="chip bg-ink-50 text-ink-700" title={info?.by ? `${info.by}님이 올림` : undefined}>직접 올림{info?.label ? ` · ${info.label}` : ""}</span>}
        {!busy && <UploadButton part={part} busy={uploading} onPick={(f) => void upload(f)} />}
      </figcaption>
      <DropArea enabled={IS_VIDEO(part) && !busy} busy={uploading} onFile={(f) => void upload(f)} onError={onError}>
      {done ? (
        src ? (IS_VIDEO(part)
          ? <video src={src} controls playsInline className="w-full max-w-sm rounded-xl bg-black" />
          : <img src={src} alt={uploaded ? label : `${label} (Higgsfield, AI 생성)`} className="w-full rounded-xl ring-1 ring-ink-100" />)
          : <div className="flex h-32 items-center justify-center rounded-xl bg-ink-50 text-xs text-ink-500">{failed ? "파일을 불러오지 못했어요" : "불러오는 중…"}</div>
      ) : (
        <div className={`flex h-32 items-center justify-center rounded-xl bg-ink-50 px-3 text-center text-xs text-ink-500 ${PENDING.has(info?.status ?? "") ? "animate-pulse" : ""}`}>
          {info?.status === "running" ? "Higgsfield가 만드는 중이에요 (보통 30초~2분)" : errorText(info?.error) ?? "아직 없어요"}
        </div>
      )}
      </DropArea>
    </figure>
  );
}

/** Higgsfield 웹 구독(Ultra)으로 만들기: 영상 요청과 같은 프롬프트를 복사하고 새 탭으로 Higgsfield를 연다(API 키 없음) */
function HiggsfieldWeb({ board, speed, reference }: { board: StoryboardResult; speed?: number | null; reference?: string | null }) {
  // 대본을 따라 만든 스토리보드면 장면 흐름(시각 × 배속)·대사 요지 + 글자 금지 문장(영상 요청과 같은 문장).
  // 배속·참조 방식은 기록의 영상 요약(없으면 1.0·스토리보드 그림)
  const prompt = storyboardVideoPrompt(board.style_en, board.script_used ? board.shots : null, { speed, reference });
  const [copy, setCopy] = useState<"idle" | "ok" | "failed">("idle");
  const go = async () => {
    // 복사를 먼저 한다(새 탭이 먼저 뜨면 이 화면이 포커스를 잃어 복사가 실패한다). 실패해도 새 탭은 연다
    try {
      await navigator.clipboard.writeText(prompt);
      setCopy("ok");
    } catch {
      setCopy("failed");
    }
    window.open(HIGGSFIELD_URL, "_blank", "noopener,noreferrer");
  };
  return (
    <div className="space-y-1.5 rounded-xl bg-ink-50 p-3 text-xs text-ink-700">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost text-xs" onClick={() => void go()}>영상 프롬프트 복사 + Higgsfield 열기</button>
        {copy === "ok" && <span role="status" className="text-emerald-800">프롬프트를 복사했어요</span>}
      </div>
      <p className="text-ink-500">Higgsfield 화면(웹 구독)에서 위 스토리보드 그림을 시작 이미지로 올리고 프롬프트를 붙여 넣어 만드세요. 만든 영상은 아래 칸에 끌어 놓거나 [파일 올리기]로 붙여요.</p>
      {copy === "failed" && (
        <div className="space-y-1">
          <p role="alert" className="text-amber-800">자동 복사가 안 됐어요. 아래 프롬프트를 선택해 복사하세요.</p>
          <textarea readOnly className="field h-24 w-full text-xs" value={prompt} aria-label="영상 프롬프트" onFocus={(e) => e.currentTarget.select()} />
        </div>
      )}
    </div>
  );
}

export function RenderPanel({ recordId }: { recordId: string }) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [overlayNonce, setOverlayNonce] = useState(0);

  const load = useCallback(async () => {
    try {
      setRec(await getCreativeRecord(recordId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "제작 기록을 읽지 못했어요");
    }
  }, [recordId]);
  useEffect(() => { void load(); }, [load]);

  const render = rec?.render ?? {};
  // render.qc(11/26 기술 검사)처럼 status가 없는 칸도 있다
  const pending = Object.values(render).some((p) => PENDING.has((p as { status?: string } | undefined)?.status ?? ""));
  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [pending, load]);

  const video = render.video;
  // 자막을 얹은 영상이 있으면 가장 나중 영상 칸이 그 파일을 재생한다(없으면 지금과 같다)
  const overlayVersion = `ov${overlayNonce}-${(render.overlay?.captions ?? 0)}-${(render.final_video?.sha256 ?? render.video?.sha256 ?? "").slice(0, 6)}`;
  const overlayControls = (part: "video" | "final_video") => overlayVisibleFor(part, render) && (
    <OverlayControls recordId={recordId} overlay={render.overlay} speed={render.video?.summary?.speed} shots={rec?.storyboard?.shots} showOriginal={showOriginal} canToggle={canToggleOriginal(render)}
      onToggle={() => setShowOriginal((v) => !v)} onChanged={() => { setOverlayNonce((n) => n + 1); setShowOriginal(false); void load(); }} />
  );
  const summaryLine = videoSummaryLine(video?.status, video?.summary);
  const thumb = chosenThumb(render.thumbnails); // 인스타 릴스 표지 = 고른 썸네일(안 고르면 첫 장)
  const reelCover = thumb ? { url: thumbUrl(recordId, thumb.media_id), picked: thumb.picked } : null;
  // 11/25 키프레임 방식이면 스토리보드 그림 대신 키프레임이 모두 그려져야 영상을 요청한다
  const keyframed = framesOf(render.keyframes).length > 0;
  const inputReady = keyframed ? keyframesDone(render.keyframes) : render.board_image?.status === "done";
  // 11/25 AI 가상 인물·그림 캐릭터는 원본을 참조로 써서 스토리보드 그림 없이도 요청할 수 있다(키프레임이 아닐 때)
  const canAsk = keyframed
    ? inputReady && !["awaiting_approval", "running", "done"].includes(video?.status ?? "")
    : canAskVideo(render.board_image?.status, video?.status, rec?.sheet?.kind);
  // 11/26 기술 검사 확인 필요 → [영상 다시 만들기](사람 버튼, 같은 영상 요청 길 + regenerate)
  const ask = async (regenerate = false) => {
    setAsking(true);
    setError(null);
    try {
      await requestCreativeVideo(recordId, regenerate ? { regenerate: true } : undefined);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "영상 요청을 보내지 못했어요");
    } finally {
      setAsking(false);
    }
  };

  if (!rec && !error) return <p className="text-xs text-ink-500">Higgsfield 결과를 불러오는 중…</p>;
  return (
    <section className="space-y-3 rounded-2xl bg-surface p-3 ring-1 ring-ink-100" aria-label="Higgsfield로 만든 그림과 영상">
      <p className="text-xs font-semibold text-ink-900">만든 결과 <span className="font-normal text-ink-500">· Higgsfield AI 생성 또는 직접 올림</span></p>
      <div className="grid gap-3 md:grid-cols-2">
        <Picture recordId={recordId} part="sheet_image" info={render.sheet_image} label="이미지 시트 그림" onChanged={() => void load()} onError={setError} />
        {(!keyframed || render.board_image) && <Picture recordId={recordId} part="board_image" info={render.board_image} label="스토리보드 그림" onChanged={() => void load()} onError={setError} />}
      </div>
      {keyframed && (
        <KeyframePairs recordId={recordId} state={render.keyframes} scenes={rec?.storyboard?.plan_scenes} videoStatus={video?.status}
          speed={video?.summary?.speed} onChanged={() => void load()} />
      )}
      <div className="space-y-2 border-t border-ink-100 pt-3">
        {/* 11/25 모션 참조 영상: 올리면 승인 뒤 영상 모델이 그 동작·카메라 움직임만 따라 만든다 */}
        <Picture recordId={recordId} part="motion_video" info={render.motion_video} label="모션 참조 영상 (선택)" onChanged={() => void load()} onError={setError} />
        <p className="text-xs text-ink-500">따라 할 춤·동작 영상(MP4)을 올리면 영상을 만들 때 앞 10초의 동작과 카메라 움직임만 따라요. 배경·사람·글자는 따라 하지 않아요.</p>
      </div>
      <div className="space-y-2 border-t border-ink-100 pt-3">
        {summaryLine && <p className="text-xs text-ink-700" aria-label="영상 만들기 요약">{summaryLine}</p>}
        <Picture recordId={recordId} part="video" info={video} label="스토리보드 영상" onChanged={() => void load()} onError={setError}
          playPart={playPartFor("video", render, showOriginal)} version={overlayVersion} />
        {overlayControls("video")}
        {video?.status === "done" && (
          <VideoQc qc={render.qc} videoStatus={video.status} credits={video.summary?.credits_estimate} busy={asking} onRegenerate={() => void ask(true)} />
        )}
        {video?.status === "awaiting_approval" && (
          <p className="text-xs text-ink-700"><ApprovalDeadline reviewId={video.review_id} className="mr-1" />사람 승인이 필요해요. <Link to="/safety" className="text-brand-700 underline">안전 처리 로그</Link>의 사람 결정 대기에서 승인하면 Higgsfield로 영상을 만들어요.</p>
        )}
        {/* 11/26 기한 지나 자동 취소: 같은 기록으로 영상 요청만 다시(콘티·키프레임 재사용) */}
        {timedOut(video) && canAsk && <TimedOutRetry busy={asking} onRetry={() => void ask()} />}
        {canAsk && !timedOut(video) && (
          <button type="button" className="btn-primary text-xs" disabled={asking} onClick={() => void ask()}>
            {asking ? "요청 중…" : video?.status ? "영상 다시 요청 (사람 승인)" : "영상 만들기 요청 (사람 승인)"}
          </button>
        )}
        {!canAsk && !video?.status && !inputReady && <p className="text-xs text-ink-500">{keyframed ? "키프레임이 모두 그려지면 영상을 요청할 수 있어요." : "스토리보드 그림이 끝나면 영상을 요청할 수 있어요."}</p>}
        {!keyframed && canAsk && render.board_image?.status !== "done" && usesOriginalReference(rec?.sheet?.kind) && <p className="text-xs text-ink-500">AI 가상 인물·그림 캐릭터는 올린 원본을 참조로 써서 스토리보드 그림 없이 만들어요.</p>}
        {rec?.storyboard && <HiggsfieldWeb board={rec.storyboard} speed={video?.summary?.speed} reference={video?.summary?.reference} />}
      </div>
      <div className="border-t border-ink-100 pt-3">
        <Picture recordId={recordId} part="final_video" info={render.final_video} label="완성 영상" onChanged={() => void load()} onError={setError}
          playPart={playPartFor("final_video", render, showOriginal)} version={overlayVersion} />
        {overlayControls("final_video")}
      </div>
      <ThumbnailPicker recordId={recordId} state={render.thumbnails} youtubeStatus={render.youtube?.status} instagramStatus={render.instagram?.status}
        hasVideo={render.video?.status === "done" || render.final_video?.status === "done"} onChanged={() => void load()} />
      <YoutubePanel recordId={recordId} youtube={render.youtube} hasVideo={render.video?.status === "done"}
        hasFinal={render.final_video?.status === "done"} onChanged={() => void load()} />
      <InstagramPanel recordId={recordId} instagram={render.instagram} hasVideo={render.video?.status === "done"}
        hasFinal={render.final_video?.status === "done"} cover={reelCover} onChanged={() => void load()} />
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </section>
  );
}
