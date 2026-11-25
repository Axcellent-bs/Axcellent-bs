import { useState } from "react";
import { useAuthedMedia } from "@/lib/authedMedia";
import { renderErrorText } from "@/lib/creativeCardView";
import { assetUrl, redrawKeyframe } from "@/lib/creativeRecords";
import { videoSummaryLine, type CreativeVideoSummary } from "@/lib/creativeVideo";
import {
  canRedraw, frameTime, framesOf, keyframePart, keyframesStatusText, layoutSrc, redrawConfirmText, sceneActions,
  type KeyframeFrame, type KeyframesState, type PlanScene,
} from "@/lib/keyframes";
import { redrawFailure, redrawNext, type RedrawStep } from "@/lib/storyboardSvg";

// 키프레임 영상(11/25, 시안 ④): 프레임마다 [SVG 구도 | 실사 키프레임] 쌍 · 시각 · [다시 그리기].
// 아래에 장면 동작 문장, 영상 요약(모델 · 만들 길이 → 배속 · 비율 · 예상 크레딧).
// 기록에 키프레임이 없으면(옛 기록·키프레임 모델이 아님) 아무것도 그리지 않는다(지금 화면 그대로).
// [다시 그리기]는 브라우저 확인창 없이 화면 안에서 한 번 더 묻는다("그림 크레딧 약 1.5를 써요").

const STATUS_KO: Record<string, string> = {
  pending: "기다리는 중", running: "그리는 중", done: "완료", failed: "실패", blocked: "막힘", unavailable: "설정 없음",
};

function FramePhoto({ recordId, frame }: { recordId: string; frame: KeyframeFrame }) {
  const done = frame.status === "done" && !!frame.sha256;
  const { src, failed } = useAuthedMedia(done ? assetUrl(recordId, keyframePart(frame.index), frame.sha256 ?? null) : null);
  if (done && src) return <img src={src} alt={`키프레임 ${frame.index + 1} (Higgsfield, AI 생성)`} className="aspect-[9/16] w-full rounded-lg object-cover ring-1 ring-ink-100" />;
  const text = done ? (failed ? "파일을 불러오지 못했어요" : "불러오는 중…")
    : frame.status === "running" ? "그리는 중…" : renderErrorText(frame.error) ?? STATUS_KO[frame.status ?? ""] ?? "아직 없어요";
  return (
    <div className={`flex aspect-[9/16] w-full items-center justify-center rounded-lg bg-ink-50 px-1 text-center text-[11px] text-ink-500 ${frame.status === "running" ? "animate-pulse" : ""}`}>{text}</div>
  );
}

function RedrawFrame({ recordId, frame, onChanged }: { recordId: string; frame: KeyframeFrame; onChanged: () => void }) {
  const [step, setStep] = useState<RedrawStep>("idle");
  const [error, setError] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const go = async () => {
    setStep((s) => redrawNext(s, "ok"));
    setError(null);
    try {
      await redrawKeyframe(recordId, frame.index);
      onChanged();
    } catch (e) {
      const f = redrawFailure(e);
      if (f.hide) setHidden(true);
      setError(f.message);
    } finally {
      setStep((s) => redrawNext(s, "done"));
    }
  };
  if (hidden) return null;
  return (
    <div className="space-y-1">
      {step === "idle" && <button type="button" className="btn-ghost w-full px-2 py-1 text-[11px]" onClick={() => setStep((s) => redrawNext(s, "click"))}>다시 그리기</button>}
      {step === "confirm" && (
        <div role="group" aria-label={`키프레임 ${frame.index + 1} 다시 그리기 확인`} className="space-y-1 rounded-lg bg-amber-50 p-1.5 text-[11px] text-amber-900">
          <p>{redrawConfirmText(frame.credits_estimate)}</p>
          <div className="flex gap-1">
            <button type="button" className="btn-primary px-2 py-0.5 text-[11px]" onClick={() => void go()}>그리기</button>
            <button type="button" className="btn-ghost px-2 py-0.5 text-[11px]" onClick={() => setStep((s) => redrawNext(s, "cancel"))}>취소</button>
          </div>
        </div>
      )}
      {step === "running" && <p role="status" className="text-[11px] text-ink-500">요청 중…</p>}
      {error && <p role="alert" className="text-[11px] text-accent">{error}</p>}
    </div>
  );
}

export function KeyframePairs({ recordId, state, scenes, videoStatus, summary, speed, onChanged }: {
  recordId: string;
  state: KeyframesState | null | undefined;
  scenes?: PlanScene[] | null;
  videoStatus?: string | null;
  summary?: CreativeVideoSummary | null;
  speed?: number | null;
  onChanged: () => void;
}) {
  const frames = framesOf(state);
  if (!frames.length) return null;
  const actions = sceneActions(scenes, speed);
  const status = keyframesStatusText(state);
  const line = videoSummaryLine(videoStatus, summary);
  return (
    <section aria-label="키프레임" className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-700">
        키프레임 <span className="font-normal text-ink-500">· 왼쪽 구도(기획 SVG) → 오른쪽 실사(AI 생성)</span>
        {status && <span className="chip bg-ink-50 text-ink-700">{status}</span>}
      </p>
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {frames.map((f) => {
          const layout = layoutSrc(f);
          return (
            <li key={f.index} className="min-w-0 space-y-1 rounded-xl border border-ink-100 bg-surface p-1.5">
              <p className="flex items-center justify-between text-[11px] text-ink-700">
                <b className="font-semibold text-ink-900">{f.index + 1}번 · {frameTime(f.at_s, speed)}</b>
                <span className="text-ink-500">{STATUS_KO[f.status ?? ""] ?? ""}</span>
              </p>
              <div className="grid grid-cols-2 gap-1">
                {layout
                  ? <img src={layout} alt={`키프레임 ${f.index + 1} 구도`} className="aspect-[9/16] w-full rounded-lg bg-white object-contain ring-1 ring-ink-100" />
                  : <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg bg-ink-50 px-1 text-center text-[10px] text-ink-500">구도 없음 · 글로만 그림</div>}
                <FramePhoto recordId={recordId} frame={f} />
              </div>
              {f.text_ko && <p className="break-words text-[11px] text-ink-700">{f.text_ko}</p>}
              {canRedraw(f, videoStatus) && <RedrawFrame recordId={recordId} frame={f} onChanged={onChanged} />}
            </li>
          );
        })}
      </ol>
      {actions.length > 0 && (
        <ul aria-label="장면 동작" className="space-y-0.5 text-[11px] text-ink-700">
          {actions.map((a) => <li key={a}>{a}</li>)}
        </ul>
      )}
      {line && <p className="text-xs text-ink-700" aria-label="키프레임 영상 요약">{line}</p>}
    </section>
  );
}
