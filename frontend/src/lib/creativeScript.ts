// 마케팅 영상 대본(스크립트) — 순수 계산만 한다: `npm test`(node --test)에서 그대로 돈다 (11/23).
// 흐름: 캐릭터 이미지 → 이미지 시트 → (줄거리가 있으면) 대본 → 대본 장면을 따라 스토리보드 → 대본+스토리보드로 영상.
// 서버 쪽은 마케팅 `creative_script`(AI로 쓰기)·`creative_script_save`(사람이 고친 값 저장), 계약은
// `ax_contracts.schemas.marketing_script`. API 부르기는 creativeScriptApi.ts.

export interface ScriptScene {
  no: number;
  start_s: number;
  end_s: number;
  visual_ko: string; // 화면 설명
  speaker_ko: string; // 말하는 사람(내레이션이면 빈 칸)
  line_ko: string; // 대사·내레이션
  caption_ko: string; // 자막(화면 글자)
}

export interface CreativeScript {
  title_ko: string;
  total_seconds: number;
  scenes: ScriptScene[];
  cta_ko: string;
  /** llm: AI가 씀 · rule: AI 없이 줄거리 문장을 나눔 · human: 사람이 고침 */
  source: "llm" | "rule" | "human";
  /** 줄거리에 없는 숫자(가격·할인·%)가 있어 뺀 문장 */
  dropped_ko: string[];
  edited_by?: string | null;
  at?: string | null;
}

export const SCRIPT_SECONDS = [15, 30, 60] as const;
export type ScriptSeconds = (typeof SCRIPT_SECONDS)[number];
export const DEFAULT_SCRIPT_SECONDS: ScriptSeconds = 30;
/** 계약 상한(marketing_script.py)과 같다 */
export const SCRIPT_LIMITS = { title: 80, visual: 200, speaker: 40, line: 200, caption: 60, cta: 120, scenes: 12 } as const;

const SOURCE_LABEL: Record<CreativeScript["source"], string> = {
  llm: "AI가 쓴 대본",
  rule: "AI 없이 줄거리 문장으로 나눈 대본",
  human: "사람이 고친 대본",
};
export const sourceLabel = (s: CreativeScript["source"]) => SOURCE_LABEL[s] ?? s;

const num = (x: number) => String(Math.round(x * 10) / 10);
/** "0~5초" */
export const spanLabel = (s: Pick<ScriptScene, "start_s" | "end_s">) => `${num(s.start_s)}~${num(s.end_s)}초`;

/** 장면 하나를 고친 새 대본(원래 값은 바꾸지 않는다) */
export function updateScene(script: CreativeScript, index: number, patch: Partial<ScriptScene>): CreativeScript {
  return { ...script, scenes: script.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
}

/** 저장할 수 없는 이유(한국어). 저장할 수 있으면 null. 서버가 같은 상한으로 다시 확인한다 */
export function scriptProblem(script: CreativeScript): string | null {
  if (!script.title_ko.trim()) return "제목을 적어 주세요";
  if (script.title_ko.length > SCRIPT_LIMITS.title) return `제목은 ${SCRIPT_LIMITS.title}자까지예요`;
  if (script.scenes.length === 0) return "장면이 하나는 있어야 해요";
  if (script.scenes.length > SCRIPT_LIMITS.scenes) return `장면은 ${SCRIPT_LIMITS.scenes}개까지예요`;
  if (script.cta_ko.length > SCRIPT_LIMITS.cta) return `끝 문장(CTA)은 ${SCRIPT_LIMITS.cta}자까지예요`;
  for (const [i, s] of script.scenes.entries()) {
    const n = i + 1;
    if (!s.visual_ko.trim()) return `${n}번 장면의 화면 설명을 적어 주세요`;
    if (!(Number.isFinite(s.start_s) && Number.isFinite(s.end_s)) || s.start_s < 0 || s.end_s > 600) return `${n}번 장면의 시간을 확인해 주세요`;
    if (s.end_s <= s.start_s) return `${n}번 장면은 끝 시간이 시작보다 뒤여야 해요`;
    if (s.visual_ko.length > SCRIPT_LIMITS.visual || s.line_ko.length > SCRIPT_LIMITS.line
      || s.caption_ko.length > SCRIPT_LIMITS.caption || s.speaker_ko.length > SCRIPT_LIMITS.speaker) {
      return `${n}번 장면의 글이 너무 길어요`;
    }
  }
  return null;
}

export interface ScriptSaveBody {
  title_ko: string;
  cta_ko: string;
  scenes: Omit<ScriptScene, "no">[];
}

/** POST …/script/save 본문. 글 앞뒤 빈칸을 지우고 장면 번호는 서버가 1부터 다시 매긴다 */
export function saveBody(script: CreativeScript): ScriptSaveBody {
  return {
    title_ko: script.title_ko.trim(),
    cta_ko: script.cta_ko.trim(),
    scenes: script.scenes.map((s) => ({
      start_s: s.start_s,
      end_s: s.end_s,
      visual_ko: s.visual_ko.trim(),
      speaker_ko: s.speaker_ko.trim(),
      line_ko: s.line_ko.trim(),
      caption_ko: s.caption_ko.trim(),
    })),
  };
}

/** [AI로 다시 쓰기] 장면 수: 6·9·12 중 가장 가까운 값(서버 경로가 이 셋만 받는다) */
export function nearestCount(n: number): 6 | 9 | 12 {
  const counts = [6, 9, 12] as const;
  return counts.reduce((best, c) => (Math.abs(c - n) < Math.abs(best - n) ? c : best), counts[0]);
}

/** 제작 기록 목록의 단계 칩: 대본·스토리보드(없으면 "시트만") */
export function stageChips(r: { script_scenes?: number; shot_count: number }): string[] {
  const chips: string[] = [];
  if (r.script_scenes) chips.push(`대본 ${r.script_scenes}장면`);
  if (r.shot_count) chips.push(`스토리보드 ${r.shot_count}칸`);
  return chips.length ? chips : ["시트만"];
}

/** 스토리보드가 대본을 따르지 않거나, 대본을 스토리보드보다 나중에 고쳤다 (11/25, 영상은 스토리보드 장면으로 만든다) */
export function boardBehindScript(rec: {
  script?: Pick<CreativeScript, "at"> | null;
  storyboard?: { script_used?: boolean } | null;
  storyboard_at?: string | null;
}): boolean {
  if (!rec.script || !rec.storyboard) return false;
  if (!rec.storyboard.script_used) return true;
  const scriptAt = rec.script.at ? Date.parse(rec.script.at) : NaN;
  const boardAt = rec.storyboard_at ? Date.parse(rec.storyboard_at) : NaN;
  return Number.isFinite(scriptAt) && Number.isFinite(boardAt) && scriptAt > boardAt;
}

/** 대본은 있는데 스토리보드가 아직 없다 (11/26, 이미지 시트 → 대본까지만 만든 기록). 처음 만들기 버튼을 띄운다 */
export function boardMissing(rec: {
  script?: unknown;
  storyboard?: unknown;
}): boolean {
  return !!rec.script && !rec.storyboard;
}

/** 11/25 뒤에서 만든 스토리보드가 기록에 붙었나: 시작한 뒤의 storyboard_at이고 대본을 따른다 */
export function boardArrived(rec: { storyboard?: { script_used?: boolean } | null; storyboard_at?: string | null }, startedAt: number): boolean {
  const at = rec.storyboard_at ? Date.parse(rec.storyboard_at) : NaN;
  return Number.isFinite(at) && at >= startedAt && rec.storyboard?.script_used === true;
}
