import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useAuthedMedia } from "@/lib/authedMedia";
import { YoutubePanel } from "./YoutubePanel";
import { assetUrl, getCreativeRecord, requestCreativeVideo, uploadCreativeFile, type CreativeRecord, type RenderPart, type RenderPartKey } from "@/lib/creativeRecords";

// 제작 기록 한 건의 Higgsfield 결과: 이미지 시트 그림 · 스토리보드 그림 · 스토리보드 영상(사람 승인 뒤).
// 다른 도구로 만든 결과(그림·완성 영상)는 칸마다 [파일 올리기]로 직접 붙인다.
// SNS·미디어 카드, 제작 기록 상세, AI 대화방 카드가 같이 쓴다. 만드는 중·승인 대기면 5초마다 다시 읽는다
// (기록을 읽을 때 마케팅이 영상 승인 여부를 다시 확인해 승인됐으면 만들기 시작한다).

const POLL_MS = 5_000;
const PENDING = new Set(["running", "awaiting_approval", "uploading"]);
const STATUS_KO: Record<string, string> = {
  running: "만드는 중", done: "완료", failed: "실패", blocked: "막힘", unavailable: "설정 없음",
  awaiting_approval: "사람 승인 대기", rejected: "거절됨",
};
const ERROR_KO: Record<string, string> = {
  higgsfield_key_missing: "Higgsfield 키가 서버에 없어요",
  model_not_configured: "Higgsfield 모델 경로가 서버에 설정되지 않았어요",
  quota_exceeded: "이번 달 사용량 예산을 넘었어요",
  quota_not_configured: "월 사용량 예산이 설정되지 않았어요",
  sheet_image_missing: "이미지 시트 그림이 없어 만들 수 없어요",
  interrupted: "서버가 다시 켜져 중단됐어요",
};
const errorText = (code?: string | null) => (code ? ERROR_KO[code] ?? code : null);

function Status({ part }: { part?: RenderPart | null }) {
  const s = part?.status || "";
  if (!s) return <span className="chip bg-ink-50 text-ink-500">대기</span>;
  const tone = s === "done" ? "bg-emerald-50 text-emerald-800" : PENDING.has(s) ? "bg-brand-50 text-brand-700" : s === "unavailable" ? "bg-ink-50 text-ink-500" : "bg-amber-50 text-amber-800";
  return <span className={`chip ${tone}`}>{STATUS_KO[s] ?? s}</span>;
}

const IS_VIDEO = (part: RenderPartKey) => part === "video" || part === "final_video";

function UploadButton({ recordId, part, onDone, onError }: { recordId: string; part: RenderPartKey; onDone: () => void; onError: (m: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    try {
      await uploadCreativeFile(recordId, part, file);
      onDone();
    } catch (e) {
      onError(e instanceof Error ? e.message : "파일을 올리지 못했어요");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <>
      <input ref={input} type="file" className="hidden" accept={IS_VIDEO(part) ? "video/mp4" : "image/jpeg,image/png"} onChange={(e) => void pick(e.target.files?.[0])} />
      <button type="button" className="btn-ghost ml-auto text-xs" disabled={busy} onClick={() => input.current?.click()}>{busy ? "올리는 중…" : "파일 올리기"}</button>
    </>
  );
}

function Picture({ recordId, part, info, label, onChanged, onError }: { recordId: string; part: RenderPartKey; info?: RenderPart | null; label: string; onChanged: () => void; onError: (m: string) => void }) {
  const done = info?.status === "done" && !!info.sha256;
  const uploaded = info?.source === "upload";
  const busy = info?.status === "running" || info?.status === "awaiting_approval";
  const { src, failed } = useAuthedMedia(done ? assetUrl(recordId, part, info?.sha256 ?? null) : null);
  return (
    <figure className="space-y-1">
      <figcaption className="flex items-center gap-2 text-xs font-medium text-ink-700">
        {label} <Status part={info} />
        {uploaded && <span className="chip bg-ink-50 text-ink-700" title={info?.by ? `${info.by}님이 올림` : undefined}>직접 올림{info?.label ? ` · ${info.label}` : ""}</span>}
        {!busy && <UploadButton recordId={recordId} part={part} onDone={onChanged} onError={onError} />}
      </figcaption>
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
    </figure>
  );
}

export function RenderPanel({ recordId }: { recordId: string }) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

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
  const pending = Object.values(render).some((p) => PENDING.has(p?.status ?? ""));
  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [pending, load]);

  const video = render.video;
  const canAsk = render.board_image?.status === "done" && !["awaiting_approval", "running", "done"].includes(video?.status ?? "");
  const ask = async () => {
    setAsking(true);
    setError(null);
    try {
      await requestCreativeVideo(recordId);
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
        <Picture recordId={recordId} part="board_image" info={render.board_image} label="스토리보드 그림" onChanged={() => void load()} onError={setError} />
      </div>
      <div className="space-y-2 border-t border-ink-100 pt-3">
        <Picture recordId={recordId} part="video" info={video} label="스토리보드 영상" onChanged={() => void load()} onError={setError} />
        {video?.status === "awaiting_approval" && (
          <p className="text-xs text-ink-700">사람 승인이 필요해요. <Link to="/safety" className="text-brand-700 underline">안전 처리 로그</Link>의 사람 결정 대기에서 승인하면 Higgsfield로 영상을 만들어요.</p>
        )}
        {canAsk && (
          <button type="button" className="btn-primary text-xs" disabled={asking} onClick={() => void ask()}>
            {asking ? "요청 중…" : video?.status ? "영상 다시 요청 (사람 승인)" : "영상 만들기 요청 (사람 승인)"}
          </button>
        )}
        {!canAsk && !video?.status && render.board_image?.status !== "done" && <p className="text-xs text-ink-500">스토리보드 그림이 끝나면 영상을 요청할 수 있어요.</p>}
      </div>
      <div className="border-t border-ink-100 pt-3">
        <Picture recordId={recordId} part="final_video" info={render.final_video} label="완성 영상" onChanged={() => void load()} onError={setError} />
      </div>
      <YoutubePanel recordId={recordId} youtube={render.youtube} hasVideo={render.video?.status === "done"}
        hasFinal={render.final_video?.status === "done"} onChanged={() => void load()} />
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </section>
  );
}
