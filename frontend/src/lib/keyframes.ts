// 키프레임 영상(11/25)의 순수 계산. node --test (keyframes.test.ts)
// 기록 render.keyframes = {status, frames: [{index, at_s, scene_n, text_ko, camera_en, svg, prompt_en,
//   layout_asset, asset, sha256, status, error, credits_estimate, attempt}], approved_at}
// 기획 장면(storyboard.plan_scenes)의 동작 문장(action_en·camera_en)을 장면별 한 줄로 보여 준다.
// 필드가 없으면(옛 기록·키프레임 모델이 아님) 화면은 지금 그대로다.
// SVG 구도는 늘 <img src="data:image/svg+xml;base64,…">로만 보여 준다(svgDataUri).
import { svgDataUri } from "./storyboardSvg.ts";

export interface KeyframeFrame {
  index: number;
  at_s?: number | null;
  scene_n?: number | null;
  text_ko?: string | null;
  camera_en?: string | null;
  svg?: string | null;
  prompt_en?: string | null;
  /** 구도 출처: 기획 SVG("planning") · 없음("none" = 프롬프트만으로 그림) */
  svg_source?: "planning" | "none" | string | null;
  layout_asset?: string | null;
  layout_png_media_id?: string | null;
  asset?: string | null;
  media_id?: string | null;
  sha256?: string | null;
  status?: string | null;
  error?: string | null;
  credits_estimate?: number | null;
  attempt?: number | null;
}

export interface KeyframesState {
  status?: string | null;
  frames?: KeyframeFrame[] | null;
  aspect?: string | null;
  approved_at?: string | null;
}

export interface PlanScene {
  n?: number | null;
  start_s?: number | null;
  end_s?: number | null;
  action_en?: string | null;
  camera_en?: string | null;
  visual?: string | null;
}

export type KeyframePart = `keyframe_${number}`;
export const keyframePart = (index: number): KeyframePart => `keyframe_${index}`;

/** 다시 그리기 경로: POST …/creative-records/{id}/keyframes/{index}/redraw (그림 1장, 그림 크레딧) */
export const keyframeRedrawPath = (recordId: string, index: number): string =>
  `/api/dashboard/marketing/creative-records/${encodeURIComponent(recordId)}/keyframes/${index}/redraw`;

const num = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));

/** 보여 줄 프레임 목록. 2장 미만이거나 없으면 빈 목록(지금 화면) */
export function framesOf(state: KeyframesState | null | undefined): KeyframeFrame[] {
  const frames = Array.isArray(state?.frames) ? state.frames.filter((f) => f && typeof f.index === "number") : [];
  return frames.length >= 2 ? frames : [];
}

/** 만든 영상 기준 시각 글: 기획 시각 × 배속 ("0초", "3.6초") */
export function frameTime(at: number | null | undefined, speed?: number | null): string {
  const s = typeof speed === "number" && speed >= 1 ? speed : 1;
  return `${num((typeof at === "number" ? at : 0) * s)}초`;
}

/** 구도 그림(data URI). SVG가 없으면 null(= 구도 없이 프롬프트만으로 그린 장) */
export function layoutSrc(frame: KeyframeFrame): string | null {
  return typeof frame.svg === "string" && frame.svg.trim() ? svgDataUri(frame.svg) : null;
}

/** [다시 그리기] 화면 안 확인 문장. 예상 크레딧을 모르면 장 수로 */
export function redrawConfirmText(credits: number | null | undefined): string {
  return typeof credits === "number" && credits > 0 ? `그림 크레딧 약 ${num(credits)}를 써요` : "그림 크레딧 1장을 써요";
}

/** 다시 그리기 버튼을 보일지: 그 장이 그리는 중이 아니고 영상을 만들기 시작하지 않았을 때 */
export function canRedraw(frame: KeyframeFrame, videoStatus: string | null | undefined): boolean {
  if (frame.status === "running") return false;
  return !["running", "done"].includes(videoStatus ?? "");
}

/** 장면 동작 한 줄씩: "0-6초 · walks forward · slow push-in" (시각 × 배속). 동작이 빈 장면은 뺀다 */
export function sceneActions(scenes: PlanScene[] | null | undefined, speed?: number | null): string[] {
  const s = typeof speed === "number" && speed >= 1 ? speed : 1;
  return (Array.isArray(scenes) ? scenes : []).flatMap((sc) => {
    const action = (sc.action_en ?? "").trim() || (sc.visual ?? "").trim();
    if (!action) return [];
    const time = `${num((sc.start_s ?? 0) * s)}-${num((sc.end_s ?? 0) * s)}초`;
    return [[time, action, (sc.camera_en ?? "").trim()].filter(Boolean).join(" · ")];
  });
}

/** 상태 칩 글 */
export function keyframesStatusText(state: KeyframesState | null | undefined): string | null {
  const frames = framesOf(state);
  if (!frames.length) return null;
  const done = frames.filter((f) => f.status === "done").length;
  if (done === frames.length) return `키프레임 ${frames.length}장 완료`;
  if (frames.some((f) => f.status === "running")) return `키프레임 그리는 중 (${done}/${frames.length})`;
  if (frames.some((f) => ["failed", "blocked", "unavailable"].includes(f.status ?? ""))) return `키프레임 ${frames.length - done}장 다시 그려야 해요`;
  return `키프레임 ${done}/${frames.length}`;
}

/** 키프레임이 모두 그려졌나(영상 요청 가능) */
export const keyframesDone = (state: KeyframesState | null | undefined): boolean => {
  const frames = framesOf(state);
  return frames.length > 0 && frames.every((f) => f.status === "done");
};
