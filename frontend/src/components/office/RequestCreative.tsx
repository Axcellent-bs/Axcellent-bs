import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { KeyframePairs } from "@/components/marketing/KeyframePairs";
import { SheetBoard } from "@/components/marketing/SheetBoard";
import { framesOf } from "@/lib/keyframes";
import { StoryboardView } from "@/components/marketing/StoryboardMaker";
import { approvalLink } from "@/lib/briefMedia";
import { getCreativeRecord, redrawBoardImage, requestCreativeVideo, thumbSrc, uploadCreativeFile, type CreativeRecord, type RenderPart } from "@/lib/creativeRecords";
import { acceptFor, uploadProblem } from "@/lib/higgsfieldWeb";
import { creativeButtons, creativeRecordOf, sceneCells, videoApproval, videoRejected } from "@/lib/requestCharacter";
import { ApprovalDeadline, TimedOutRetry } from "@/components/marketing/ApprovalDeadline";
import { VideoQc } from "@/components/marketing/VideoQc";
import { timedOut } from "@/lib/videoApprovalTime";
import type { VideoPlanView } from "@/lib/videoPlan";
import type { WorkRequestRow } from "@/lib/workRequests";

// 대화방 의뢰 진행 카드의 [이미지 시트][스토리보드] · 영상 승인 대기 (홍보 영상 의뢰, 11/23 시안 ②).
// - 의뢰의 제작 기록(CR-…) = 영상 준비 단계 결과 번호. 있으면 대화방 안 패널에 마케팅 1:1과 같은 SheetBoard·StoryboardView
// - 제작 기록이 없는 의뢰(캐릭터 없이)는 [스토리보드]만 두고, 기획 제작안 장면 글을 칸으로 보여 준다(그림 없음)
// - "영상 승인 대기"면 [승인 화면 열기]. 승인은 지금 승인 화면에서만 한다(여기서 바로 승인하지 않는다)

// 11/25 키프레임 영상이면 [키프레임] 탭(프레임마다 SVG 구도 | 실사 키프레임 쌍 · 다시 그리기)이 더 생긴다
type Tab = "sheet" | "storyboard" | "keyframes";
const KEYFRAME_POLL_MS = 5_000;

const records = new Map<string, Promise<CreativeRecord>>();
function loadRecord(id: string, version: string): Promise<CreativeRecord> {
  const key = `${id}:${version}`;
  let p = records.get(key);
  if (!p) {
    p = getCreativeRecord(id);
    p.catch(() => records.delete(key));
    records.set(key, p);
  }
  return p;
}

function Panel({ recordId, plan, tab, setTab, version }: {
  recordId: string | null; plan: Pick<VideoPlanView, "scenes"> | null; tab: Tab; setTab: (t: Tab) => void; version: string;
}) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0); // 키프레임 다시 그리기·그리는 중이면 다시 읽는다
  useEffect(() => {
    if (!recordId) return;
    let live = true;
    loadRecord(recordId, `${version}:${nonce}`).then((r) => live && setRec(r), (e: unknown) => live && setError(e instanceof Error ? e.message : "기록을 읽지 못했어요"));
    return () => { live = false; };
  }, [recordId, version, nonce]);
  const keyframes = rec?.render?.keyframes;
  const keyframed = framesOf(keyframes).length > 0;
  const drawing = framesOf(keyframes).some((f) => f.status === "running");
  useEffect(() => {
    if (!drawing) return;
    const t = window.setTimeout(() => setNonce((n) => n + 1), KEYFRAME_POLL_MS);
    return () => window.clearTimeout(t);
  }, [drawing, nonce]);

  const tabs: Tab[] = recordId ? ["sheet", "storyboard", ...(keyframed ? ["keyframes" as const] : [])] : ["storyboard"];
  return (
    <section aria-label="이미지 시트·스토리보드" className="w-full overflow-hidden rounded-xl border border-ink-100 bg-surface">
      <div role="tablist" className="flex border-b border-ink-100 text-[12.5px]">
        {tabs.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`border-b-2 px-3 py-1.5 ${tab === t ? "border-brand-600 font-semibold text-brand-700" : "border-transparent text-ink-500"}`}>
            {t === "sheet" ? "이미지 시트" : t === "keyframes" ? "키프레임" : "스토리보드"}
          </button>
        ))}
      </div>
      <div className="max-h-[32rem] overflow-y-auto p-2.5">
        {recordId && error && <p className="text-xs text-red-700">{error}</p>}
        {recordId && !rec && !error && <p className="animate-pulse text-xs text-ink-500">이미지 시트를 불러오는 중…</p>}
        {recordId && rec && tab === "sheet" && <SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} />}
        {recordId && rec && tab === "storyboard" && (rec.storyboard
          ? <StoryboardView r={rec.storyboard} onRedraw={async () => { await redrawBoardImage(rec.record_id); }} />
          : <p className="text-xs text-ink-500">스토리보드를 아직 붙이지 않았어요</p>)}
        {recordId && rec && tab === "keyframes" && (
          <KeyframePairs recordId={recordId} state={keyframes} scenes={rec.storyboard?.plan_scenes}
            videoStatus={rec.render?.video?.status} summary={rec.render?.video?.summary} speed={rec.render?.video?.summary?.speed}
            onChanged={() => setNonce((n) => n + 1)} />
        )}
        {!recordId && plan && (
          <ol className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {sceneCells(plan).map((c) => (
              <li key={c.key} className="min-w-0 rounded-lg border border-ink-100 bg-ink-50 p-2 text-xs">
                <b className="block text-ink-900">{c.title}</b>
                <span className="block break-words text-ink-700">{c.text}</span>
                {c.caption && <span className="mt-0.5 block break-words text-ink-500">자막 · {c.caption}</span>}
              </li>
            ))}
          </ol>
        )}
        {!recordId && <p className="mt-1.5 text-xs text-ink-500">캐릭터 없이 만드는 의뢰라 기획 제작안의 장면 글만 보여요(그림 없음).</p>}
      </div>
    </section>
  );
}

/** 11/25 영상 승인 대기 동안 모션 참조 영상(MP4)을 올린다. 승인 뒤 영상이 그 동작만 따라 만든다 */
function MotionUpload({ recordId }: { recordId: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);
  const pick = async (file: File) => {
    const problem = uploadProblem("motion_video", file);
    if (problem) { setError(problem); return; }
    setState("busy");
    setError(null);
    try {
      await uploadCreativeFile(recordId, "motion_video", file);
      setState("done");
    } catch (e) {
      setState("idle");
      setError(e instanceof Error ? e.message : "모션 참조 영상을 올리지 못했어요");
    }
  };
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <input ref={input} type="file" className="hidden" accept={acceptFor("motion_video")}
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void pick(f); }} />
      <button type="button" className="btn-ghost btn-card" disabled={state === "busy"} onClick={() => input.current?.click()}>
        {state === "busy" ? "올리는 중…" : state === "done" ? "모션 참조 영상 다시 올리기" : "모션 참조 영상 올리기 (선택)"}
      </button>
      {state === "done" && <span className="text-[11px] text-ink-500">올렸어요. 승인하면 이 동작을 따라 만들어요.</span>}
      {error && <span className="text-[11px] text-red-700" role="alert">{error}</span>}
    </span>
  );
}

/** 11/26 영상 승인이 기한 지나 취소됐으면 [다시 승인 요청]. 같은 제작 기록으로 영상 요청만 다시 한다(콘티·키프레임 재사용) */
function VideoRetry({ recordId, version }: { recordId: string; version: string }) {
  const [video, setVideo] = useState<RenderPart | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadRecord(recordId, version).then((r) => live && setVideo(r.render?.video ?? null), () => undefined);
    return () => { live = false; };
  }, [recordId, version]);
  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      setVideo((await requestCreativeVideo(recordId)).video);
    } catch (e) {
      setError(e instanceof Error ? e.message : "다시 승인 요청을 보내지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  if (video?.status === "awaiting_approval") {
    return (
      <p className="flex flex-wrap items-center gap-1.5">
        <ApprovalDeadline reviewId={video.review_id} />
        <Link className="btn-ghost btn-card inline-flex items-center" to={approvalLink(video.review_id ?? null)}>승인 화면 열기</Link>
        <span className="text-[11px] text-ink-500">다시 요청했어요. 승인하면 제작 기록에서 영상을 만들어요.</span>
      </p>
    );
  }
  if (!timedOut(video)) return null;
  return (
    <div className="space-y-1">
      <TimedOutRetry busy={busy} onRetry={() => void retry()} />
      {error && <p className="text-[11px] text-red-700" role="alert">{error}</p>}
    </div>
  );
}

/** 11/26 ⑦ 영상 단계: 다 만든 영상의 기술 검사 칩·표. 확인 필요면 [영상 다시 만들기](같은 영상 요청 길 + regenerate) */
function VideoQcStage({ recordId, version }: { recordId: string; version: string }) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadRecord(recordId, version).then((r) => live && setRec(r), () => undefined);
    return () => { live = false; };
  }, [recordId, version]);
  const video = rec?.render?.video;
  if (video?.status !== "done" || !rec?.render?.qc) return null;
  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = (await requestCreativeVideo(recordId, { regenerate: true })).video;
      setRec((cur) => (cur ? { ...cur, render: { ...cur.render, video: got } } : cur));
    } catch (e) {
      setError(e instanceof Error ? e.message : "영상 다시 만들기 요청을 보내지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  return <VideoQc qc={rec.render.qc} videoStatus={video.status} credits={video.summary?.credits_estimate} busy={busy} error={error} regenerate={video.regenerate} onRegenerate={() => void regenerate()} />;
}

export function RequestCreative({ row, plan }: { row: WorkRequestRow; plan: Pick<VideoPlanView, "scenes"> | null }) {
  const [open, setOpen] = useState<Tab | null>(null);
  const buttons = creativeButtons(row, plan);
  const approval = videoApproval(row);
  const recordId = creativeRecordOf(row);
  const closed = videoRejected(row) && !!recordId;
  if (!buttons.sheet && !buttons.storyboard && !approval && !closed && !recordId) return null;
  const toggle = (t: Tab) => setOpen((cur) => (cur === t ? null : t));
  return (
    <div className="space-y-2">
      {approval && (
        <p className="flex flex-wrap items-center gap-1.5">
          <ApprovalDeadline reviewId={approval.reviewId} />
          <Link className="btn-ghost btn-card inline-flex items-center" to={approvalLink(approval.reviewId)}>승인 화면 열기</Link>
          {recordId && <MotionUpload recordId={recordId} />}
        </p>
      )}
      {closed && recordId && <VideoRetry recordId={recordId} version={row.updated_at} />}
      {recordId && !closed && !approval && <VideoQcStage recordId={recordId} version={row.updated_at} />}
      {(buttons.sheet || buttons.storyboard) && (
        <span className="flex flex-wrap gap-1.5">
          {buttons.sheet && <button type="button" aria-expanded={open === "sheet"} className={`btn-card ${open === "sheet" ? "btn-primary" : "btn-ghost"}`} onClick={() => toggle("sheet")}>이미지 시트</button>}
          {buttons.storyboard && <button type="button" aria-expanded={open === "storyboard"} className={`btn-card ${open === "storyboard" ? "btn-primary" : "btn-ghost"}`} onClick={() => toggle("storyboard")}>스토리보드</button>}
        </span>
      )}
      {open && <Panel recordId={recordId} plan={plan} tab={recordId ? open : "storyboard"} setTab={setOpen} version={row.updated_at} />}
    </div>
  );
}
