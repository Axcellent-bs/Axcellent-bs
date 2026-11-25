import { useState } from "react";
import type { StoryboardResult, StoryboardShot } from "@/lib/storyboard";
import { isSvgBoard, redrawFailure, redrawNext, svgCell, type RedrawStep } from "@/lib/storyboardSvg";
import { spanLabel } from "@/lib/creativeScript";
import { CopyButton, PromptBox } from "./PromptParts";

// 마케팅 › SNS·미디어 › 스토리보드 결과 보기 (CharacterSheetMaker가 만든 결과를 그린다).
// 칸마다 번호·샷 크기·카메라 앵글·장면·화면 배치와 구도 그림을 보여 주고, 칸별·시트 한 장 프롬프트를 복사하게 한다.
// 대본을 따라 만든 스토리보드(script_used)면 칸마다 대본 장면의 시간·대사·자막도 보인다(11/23).

const HORIZON: Record<string, number> = { eye_level: 52, low: 72, high: 26, ground: 82, dutch: 52, birds_eye: -10 };

function Person({ cx, top, h }: { cx: number; top: number; h: number }) {
  const r = h * 0.13;
  return (
    <g className="fill-ink-500">
      <circle cx={cx} cy={top + r} r={r} />
      <rect x={cx - h * 0.17} y={top + r * 2.1} width={h * 0.34} height={h - r * 2.1} rx={h * 0.08} />
    </g>
  );
}

/** 샷 크기는 인물 크기로, 앵글은 지평선 높이·기울기로 그린 구도 그림 */
function Subject({ shot, y }: { shot: StoryboardShot; y: number }) {
  const c = shot.has_character;
  switch (shot.shot_size) {
    case "extreme_wide": return c ? <Person cx={112} top={y - 8} h={9} /> : null;
    case "wide": return c ? <Person cx={100} top={y - 18} h={24} /> : null;
    case "full": return c ? <Person cx={80} top={12} h={72} /> : null;
    case "medium": return c ? <Person cx={80} top={14} h={130} /> : null;
    case "close_up": return <circle cx={80} cy={50} r={c ? 30 : 22} className={c ? "fill-ink-500" : "fill-ink-400"} />;
    case "extreme_close_up": return <circle cx={80} cy={55} r={58} className={c ? "fill-ink-500" : "fill-ink-400"} />;
    case "detail": return <rect x={52} y={30} width={56} height={40} rx={8} className="fill-ink-400" />;
    case "over_shoulder":
      return (
        <g>
          {c && <Person cx={112} top={26} h={44} />}
          <path d="M-6 92 Q8 40 46 46 Q70 50 72 92 Z" className="fill-ink-700" />
        </g>
      );
    case "pov":
      return (
        <g className="fill-ink-500">
          <ellipse cx={46} cy={92} rx={20} ry={14} />
          <ellipse cx={114} cy={92} rx={20} ry={14} />
        </g>
      );
    default: return c ? <Person cx={80} top={18} h={60} /> : null;
  }
}

function Frame({ shot }: { shot: StoryboardShot }) {
  const y = HORIZON[shot.angle] ?? 52;
  const tilt = shot.angle === "dutch" ? -8 : 0;
  return (
    <svg viewBox="0 0 160 90" className="block h-auto w-full" role="img" aria-label={`${shot.shot_size_ko} · ${shot.angle_ko} 구도`}>
      <rect width="160" height="90" className="fill-ink-50" />
      <g transform={`rotate(${tilt} 80 45)`}>
        <rect x="-20" y={y > 0 ? y : -20} width="200" height="130" className="fill-ink-100" />
        {y > 0 && <line x1="-20" x2="180" y1={y} y2={y} className="stroke-ink-200" strokeWidth="1" />}
        <Subject shot={shot} y={y} />
      </g>
      <g className="stroke-ink-200" strokeWidth="0.5" strokeDasharray="2 2" aria-hidden="true">
        <line x1="53.3" x2="53.3" y1="0" y2="90" />
        <line x1="106.6" x2="106.6" y1="0" y2="90" />
        <line x1="0" x2="160" y1="30" y2="30" />
        <line x1="0" x2="160" y1="60" y2="60" />
      </g>
    </svg>
  );
}

function ShotCard({ shot }: { shot: StoryboardShot }) {
  return (
    <li className="overflow-hidden rounded-2xl border border-ink-100 bg-white">
      <div className="relative">
        <Frame shot={shot} />
        <span className="absolute left-2 top-2 rounded-md bg-white/90 px-1.5 text-xs font-semibold tabular-nums text-ink-900 ring-1 ring-ink-100">{shot.no}</span>
      </div>
      <div className="space-y-2 p-3">
        <div className="flex flex-wrap gap-1">
          <span className="chip bg-brand-50 text-brand-700">{shot.shot_size_ko}</span>
          <span className="chip bg-ink-50 text-ink-700">{shot.angle_ko}</span>
          {!shot.has_character && <span className="chip bg-ink-50 text-ink-500">인물 없음</span>}
        </div>
        <p className="text-sm text-ink-900">{shot.scene_ko}</p>
        <p className="text-xs text-ink-500">{shot.composition_ko}</p>
        {shot.start_s != null && shot.end_s != null && (
          <div className="space-y-1 rounded-lg bg-ink-50 p-2 text-xs text-ink-700">
            <span className="tabular-nums text-ink-500">대본 {spanLabel({ start_s: shot.start_s, end_s: shot.end_s })}</span>
            {shot.line_ko && <p><span className="text-ink-500">{shot.speaker_ko || "내레이션"}:</span> “{shot.line_ko}”</p>}
            {shot.caption_ko && <p><span className="text-ink-500">자막</span> {shot.caption_ko}</p>}
          </div>
        )}
        <details className="text-xs">
          <summary className="cursor-pointer text-ink-500">이 칸 프롬프트</summary>
          <div className="mt-2 space-y-1">
            <span className="flex justify-end"><CopyButton text={shot.prompt_en} label={`${shot.no}번 칸 프롬프트`} /></span>
            <p className="whitespace-pre-wrap break-words rounded-lg bg-surface p-2 leading-relaxed text-ink-700 ring-1 ring-ink-100">{shot.prompt_en}</p>
          </div>
        </details>
      </div>
    </li>
  );
}

/** SVG 콘티 칸: AI가 그린 SVG는 <img> data URI로만 보인다(스크립트가 돌지 않는다). 그림이 없는 칸은 구도 그림 */
function SvgCellItem({ shot }: { shot: StoryboardShot }) {
  const c = svgCell(shot);
  return (
    <li className="min-w-0 space-y-1">
      <div className="relative overflow-hidden rounded-lg border border-ink-100 bg-white">
        {c.src
          ? <img src={c.src} alt={`${c.no}번 칸 콘티: ${c.caption}`} className="block h-auto w-full" />
          : <Frame shot={shot} />}
        <span className="absolute left-1.5 top-1.5 rounded-md bg-white/90 px-1.5 text-xs font-semibold tabular-nums text-ink-900 ring-1 ring-ink-100">{c.no}</span>
      </div>
      {c.time && <p className="text-xs font-medium tabular-nums text-ink-700">{c.time}</p>}
      <p className="break-words text-xs text-ink-500">{c.caption}</p>
    </li>
  );
}

/** [Higgsfield 그림으로 다시 그리기] → 같은 화면에서 한 번 더 묻고 [만들기]를 눌러야 그림 크레딧을 쓴다 */
function RedrawControl({ onRedraw }: { onRedraw: () => Promise<void> }) {
  const [step, setStep] = useState<RedrawStep>("idle");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  // 서버에 아직 전용 경로가 없거나(404) 다른 회사 기록이면 버튼을 숨긴다
  const [hidden, setHidden] = useState(false);
  const go = async () => {
    setStep((s) => redrawNext(s, "ok"));
    setError(null);
    try {
      await onRedraw();
      setSent(true);
    } catch (e) {
      const f = redrawFailure(e);
      if (f.hide) setHidden(true);
      setError(f.message);
    } finally {
      setStep((s) => redrawNext(s, "done"));
    }
  };
  if (hidden) return null;
  if (sent) return <p role="status" className="text-xs text-ink-500">그림을 요청했어요. 다 그리면 이 기록에 붙어요.</p>;
  return (
    <div className="space-y-1.5">
      {step === "idle" && <button type="button" className="btn-ghost text-xs" onClick={() => setStep((s) => redrawNext(s, "click"))}>Higgsfield 그림으로 다시 그리기</button>}
      {step === "confirm" && (
        <div role="group" aria-label="그림 크레딧 확인" className="flex flex-wrap items-center gap-2 rounded-xl bg-ink-50 p-2 text-xs text-ink-700">
          <span>그림 크레딧을 써요. 만들까요?</span>
          <button type="button" className="btn-primary text-xs" onClick={() => void go()}>만들기</button>
          <button type="button" className="btn-ghost text-xs" onClick={() => setStep((s) => redrawNext(s, "cancel"))}>취소</button>
        </div>
      )}
      {step === "running" && <p role="status" className="text-xs text-ink-500">요청하는 중…</p>}
      {error && <p role="alert" className="text-xs text-accent">{error}</p>}
    </div>
  );
}

export function StoryboardView({ r, onRedraw }: { r: StoryboardResult; onRedraw?: () => Promise<void> }) {
  const svgBoard = isSvgBoard(r);
  return (
    <div className="space-y-5">
      <div>
        <p className="text-base font-semibold text-ink-900">{r.title_ko}</p>
        <p className="text-sm text-ink-500">{r.logline_ko}{r.character_used ? " · 이미지 시트 캐릭터 사용" : ""}{r.script_used ? " · 대본 장면을 따름" : ""}</p>
        {r.cta_ko && <p className="text-xs text-ink-500">끝 문장(CTA): {r.cta_ko}</p>}
      </div>
      {svgBoard && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-ink-900">스토리보드 (AI 콘티)</span>
          <span className="chip bg-ink-50 text-ink-500">그림 크레딧 0</span>
          {onRedraw && <span className="ml-auto"><RedrawControl onRedraw={onRedraw} /></span>}
        </div>
      )}
      <ol className={svgBoard ? "grid grid-cols-2 gap-2 sm:grid-cols-4" : "grid gap-3 sm:grid-cols-2 lg:grid-cols-3"} aria-label="스토리보드 칸">
        {r.shots.map((s) => svgBoard ? <SvgCellItem key={s.no} shot={s} /> : <ShotCard key={s.no} shot={s} />)}
      </ol>
      <PromptBox title={`스토리보드 시트 한 장 프롬프트 (${r.shots.length}칸 · ${r.columns}열)`} text={r.sheet_prompt_en} />
      <PromptBox title="네거티브 프롬프트" text={r.negative_prompt_en} />
    </div>
  );
}
