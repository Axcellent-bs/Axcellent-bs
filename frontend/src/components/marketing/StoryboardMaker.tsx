import { useState } from "react";
import type { CharacterSheetResult } from "@/lib/characterSheet";
import { MAX_STORY, SHOT_COUNTS, STYLES, makeStoryboard, type ShotCount, type StoryboardResult, type StoryboardShot } from "@/lib/storyboard";
import { CopyButton, PromptBox } from "./CharacterSheetMaker";

// 마케팅 › SNS·미디어 › 스토리보드.
// 줄거리를 적으면 마케팅 AI가 장면을 나누고, 칸마다 샷 크기·카메라 앵글·화면 배치(구도)를 정한
// 스토리보드와 칸별·시트 한 장짜리 이미지 프롬프트를 만든다. 위 이미지 시트를 만들었으면 그 캐릭터
// 고정 문장(consistency_en)을 같이 보내 모든 칸이 같은 캐릭터가 되게 한다. 이미지는 만들지 않는다.

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

function Result({ r }: { r: StoryboardResult }) {
  return (
    <div className="space-y-5">
      <div>
        <p className="text-base font-semibold text-ink-900">{r.title_ko}</p>
        <p className="text-sm text-ink-500">{r.logline_ko}{r.character_used ? " · 이미지 시트 캐릭터 사용" : ""}</p>
      </div>
      <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="스토리보드 칸">
        {r.shots.map((s) => <ShotCard key={s.no} shot={s} />)}
      </ol>
      <PromptBox title={`스토리보드 시트 한 장 프롬프트 (${r.shots.length}칸 · ${r.columns}열)`} text={r.sheet_prompt_en} />
      <PromptBox title="네거티브 프롬프트" text={r.negative_prompt_en} />
    </div>
  );
}

export function StoryboardMaker({ sheet }: { sheet: CharacterSheetResult | null }) {
  const [story, setStory] = useState("");
  const [shots, setShots] = useState<ShotCount>(12);
  const [style, setStyle] = useState(STYLES[0].value);
  const [useSheet, setUseSheet] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StoryboardResult | null>(null);

  const make = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await makeStoryboard({ story, shots, style_en: style, character_en: sheet && useSheet ? sheet.consistency_en : undefined }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "스토리보드를 만들지 못했어요");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-4 p-6" aria-labelledby="storyboard-title">
      <div>
        <h2 id="storyboard-title" className="text-lg font-semibold">스토리보드 만들기</h2>
        <p className="mt-1 text-xs text-ink-500">줄거리를 적으면 장면을 나누고, 칸마다 화면을 얼마나 넓게 담을지(샷 크기)와 카메라를 어디서 볼지(앵글), 화면 안 배치를 정해요. 위에서 이미지 시트를 만들었으면 그 캐릭터 문장을 넣어 모든 칸이 같은 캐릭터가 돼요. 이미지는 만들지 않고 프롬프트만 만들어요.</p>
      </div>
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-ink-700">줄거리</span>
        <textarea value={story} maxLength={MAX_STORY} rows={3} onChange={(e) => setStory(e.target.value)} placeholder="예: 가을 들판에서 자전거를 타던 소녀가 길가의 고양이를 만나 바구니에 태우고, 노을을 보며 주먹밥을 나눠 먹는다" className="field w-full text-sm" />
        <span className="mt-1 block text-right text-xs tabular-nums text-ink-500">{story.length}/{MAX_STORY}</span>
      </label>
      <div className="flex flex-wrap items-end gap-4">
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-ink-700">칸 수</legend>
          <div className="flex gap-1">
            {SHOT_COUNTS.map((n) => (
              <button key={n} type="button" aria-pressed={shots === n} onClick={() => setShots(n)} className={shots === n ? "btn-primary text-xs" : "btn-ghost text-xs"}>{n}칸</button>
            ))}
          </div>
        </fieldset>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-700">그림체</span>
          <select value={style} onChange={(e) => setStyle(e.target.value)} className="field text-sm">
            {STYLES.map((s) => <option key={s.label} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className={`flex items-center gap-2 text-sm ${sheet ? "text-ink-700" : "text-ink-500"}`}>
          <input type="checkbox" checked={!!sheet && useSheet} disabled={!sheet} onChange={(e) => setUseSheet(e.target.checked)} />
          {sheet ? "이미지 시트 캐릭터 넣기" : "이미지 시트를 먼저 만들면 캐릭터를 넣을 수 있어요"}
        </label>
        <button type="button" className="btn-primary ml-auto" disabled={!story.trim() || busy} onClick={() => void make()}>{busy ? "만드는 중…" : "스토리보드 만들기"}</button>
      </div>
      <div className="min-h-[8rem] border-t border-ink-100 pt-4" aria-live="polite">
        {error ? <p className="text-sm text-accent" role="alert">{error}</p>
          : busy ? <p className="text-sm text-ink-500">장면을 나누고 구도를 정하는 중이에요…</p>
          : result ? <Result r={result} />
          : <p className="text-sm text-ink-500">줄거리를 적고 만들기를 누르면 여기에 칸이 나와요.</p>}
      </div>
    </section>
  );
}
