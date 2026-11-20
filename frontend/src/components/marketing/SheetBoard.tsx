import type { ReactNode } from "react";
import type { CharacterSheetResult } from "@/lib/characterSheet";

// 마케팅 › 이미지 시트를 한 장의 시트처럼 보여 준다(만든 직후 카드와 제작 기록 상세에서 같이 쓴다).
// 실제 그림이 아니라 배치판이다: 칸마다 방향·표정 자리를 캐릭터 색 팔레트로 칠한 실루엣과 이름으로
// 보여 주고, 맨 왼쪽에 올린 원본 미리보기를 둔다. 그림 생성(나노바나나)이 붙으면 이 칸에 그림이 들어간다.

const SKIN = "#f3dccb";
const INK = "#2a2a2a";

function Figure({ kind, hair, body, legs }: { kind: string; hair: string; body: string; legs: string }) {
  const side = kind === "left" || kind === "right";
  const w = side ? 14 : kind === "three_quarter" ? 18 : 22;
  const flip = kind === "right" ? "scale(-1 1) translate(-60 0)" : undefined;
  if (kind === "top") {
    return (
      <svg viewBox="0 0 60 100" className="h-24 w-auto" aria-hidden="true">
        <ellipse cx="30" cy="62" rx="20" ry="13" fill={body} />
        <circle cx="30" cy="58" r="11" fill={hair} />
      </svg>
    );
  }
  const back = kind === "back";
  return (
    <svg viewBox="0 0 60 100" className="h-24 w-auto" aria-hidden="true">
      <g transform={flip}>
        <circle cx="30" cy="16" r="10" fill={back ? hair : SKIN} />
        <path d={back ? "M20 9 Q30 2 40 9 L40 24 Q30 26 20 24 Z" : "M20 10 Q30 2 40 10 L40 14 Q30 9 20 14 Z"} fill={hair} />
        <rect x={30 - w / 2} y="28" width={w} height="34" rx="6" fill={body} />
        <rect x={30 - w / 2 + 2} y="62" width={w / 2 - 3} height="30" rx="3" fill={legs} />
        <rect x="31" y="62" width={w / 2 - 3} height="30" rx="3" fill={legs} />
        {side && <circle cx="24" cy="17" r="1.4" fill={INK} />}
        {kind === "three_quarter" && <><circle cx="27" cy="17" r="1.3" fill={INK} /><circle cx="33" cy="17" r="1.3" fill={INK} /></>}
        {(kind === "front" || kind === "bottom") && <><circle cx="26" cy="17" r="1.3" fill={INK} /><circle cx="34" cy="17" r="1.3" fill={INK} /></>}
      </g>
    </svg>
  );
}

const MOUTH: Record<string, string> = {
  smile: "M14 26 Q20 32 26 26", neutral: "M15 27 L25 27", angry: "M14 29 Q20 24 26 29", sad: "M14 29 Q20 24 26 29",
};

function Face({ id, hair }: { id: string; hair: string }) {
  return (
    <svg viewBox="0 0 40 40" className="h-14 w-14" aria-hidden="true">
      <circle cx="20" cy="21" r="15" fill={SKIN} />
      <path d="M5 18 Q20 2 35 18 L35 12 Q20 -2 5 12 Z" fill={hair} />
      <circle cx="15" cy="20" r="1.6" fill={INK} />
      <circle cx="25" cy="20" r="1.6" fill={INK} />
      {id === "angry" && <path d="M12 15 L18 17 M28 15 L22 17" stroke={INK} strokeWidth="1.4" />}
      {id === "sad" && <path d="M12 17 L18 15 M28 17 L22 15" stroke={INK} strokeWidth="1.2" />}
      {id === "surprised"
        ? <ellipse cx="20" cy="28" rx="2.6" ry="3.4" fill={INK} />
        : <path d={MOUTH[id] ?? MOUTH.neutral} stroke={INK} strokeWidth="1.6" fill="none" strokeLinecap="round" />}
    </svg>
  );
}

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <li className="flex flex-col items-center gap-1 rounded-xl bg-white p-2 ring-1 ring-ink-100">
      <div className="flex h-24 items-end justify-center">{children}</div>
      <span className="text-center text-xs text-ink-700">{label}</span>
    </li>
  );
}

export function SheetBoard({ r, thumb }: { r: CharacterSheetResult; thumb?: string | null }) {
  const hair = r.palette[0]?.hex ?? "#6b4a3a";
  const body = r.palette[1]?.hex ?? "#c9b79c";
  const legs = r.palette[2]?.hex ?? "#3a4a6b";
  const views = r.panels.filter((p) => p.kind === "view");
  const faces = r.panels.filter((p) => p.kind === "expression");
  const poses = r.panels.filter((p) => p.kind === "pose");
  return (
    <figure className="space-y-3 rounded-2xl bg-surface p-4 ring-1 ring-ink-100" aria-label="이미지 시트 배치판">
      <div className="flex flex-wrap items-start gap-4">
        {thumb && <img src={thumb} alt="올린 캐릭터 원본 미리보기" className="h-28 w-28 shrink-0 rounded-xl bg-white object-contain ring-1 ring-ink-100" />}
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-sm font-semibold text-ink-900">{r.summary_ko}</p>
          <p className="text-xs text-ink-500">키 비율 {r.proportions}{r.personality_ko ? ` · ${r.personality_ko}` : ""}</p>
          <ul className="flex flex-wrap gap-2 pt-1" aria-label="색 팔레트">
            {r.palette.map((c) => (
              <li key={c.hex + c.name} className="flex items-center gap-1.5 rounded-lg bg-white px-2 py-1 text-xs text-ink-700 ring-1 ring-ink-100">
                <span className="inline-block h-5 w-5 rounded-md ring-1 ring-ink-200" style={{ backgroundColor: c.hex }} aria-hidden="true" />
                {c.name}<span className="tabular-nums text-ink-500">{c.hex}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-ink-700">턴어라운드 {views.length}칸</p>
        <ol className="grid grid-cols-4 gap-2 sm:grid-cols-7">
          {views.map((p) => <Tile key={p.id} label={p.label_ko}><Figure kind={p.id} hair={hair} body={body} legs={legs} /></Tile>)}
        </ol>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-medium text-ink-700">표정 {faces.length}칸</p>
          <ol className="grid grid-cols-5 gap-2">
            {faces.map((p) => <Tile key={p.id} label={p.label_ko}><Face id={p.id} hair={hair} /></Tile>)}
          </ol>
        </div>
        {poses.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-ink-700">기본 포즈 {poses.length}칸</p>
            <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {poses.map((p) => (
                <li key={p.id} className="rounded-xl bg-white p-2 text-xs ring-1 ring-ink-100">
                  <span className="font-medium text-ink-900">{p.label_ko}</span>
                  <span className="mt-1 block text-ink-500">{p.prompt_en}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
      <figcaption className="text-xs text-ink-500">칸 배치와 색을 보여 주는 배치판이에요. 실제 그림은 이미지 생성(나노바나나)이 연결되면 이 칸에 들어가요.</figcaption>
    </figure>
  );
}
