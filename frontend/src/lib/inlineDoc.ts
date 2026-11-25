// 대화방 의뢰 카드 안에서 보고서·기획서를 바로 보기 (11/26). 순수 계산만 한다: `npm test`(node --test).
// - [▸ 펼쳐 보기]로 카드 안에서 연다. 처음엔 접힘, 한 번 연 뒤에는 다시 읽지 않는다(캐시). [보고서 열기]·[PDF로 저장]은 그대로 옆에
// - 보고서: 지금 보고서 화면 본문(TrendReportBody)을 읽기 전용으로 · 기획서: 카드가 이미 읽은 기획 기록의 영상 제작안(새 API 없음)
// - 기획 SVG 콘티는 늘 <img src="data:image/svg+xml;base64,…">로만 그린다(svgDataUri)

import { svgDataUri } from "./storyboardSvg.ts";
import { videoFallbackLine, videoSvgLine, type VideoPlanView, type VideoSceneView } from "./videoPlan.ts";

export const expandLabel = (open: boolean): string => (open ? "▾ 접기" : "▸ 펼쳐 보기");

export type InlineLoad<T> = { kind: "loading" } | { kind: "ok"; value: T } | { kind: "error"; message: string };

export const REPORT_LOADING = "보고서를 불러오는 중…";
export const PLAN_MISSING = "기획서에 영상 제작안이 아직 없어요. [기획서 열기]로 전체를 보세요.";

/** 불러오기 실패 문구. 서버 문장이 있으면 뒤에 붙인다 */
export const loadFailedKo = (what: "보고서" | "기획서", message?: string | null): string =>
  `${what}를 불러오지 못했어요${message ? ` (${message})` : ""}. [${what} 열기]로 다시 볼 수 있어요.`;

const secs = (x: number) => (Number.isInteger(x) ? `${x}` : x.toFixed(1));

export interface InlineScene {
  key: string;
  title: string; // "장면 1 · 0~2초"
  thumb: string | null; // 첫 화면 3D 콘티 (data URI) · 없으면 null
  thumbEnd: string | null; // 끝 화면 콘티
  frames: string | null; // "첫 화면 → 끝 화면"
  visual: string;
  action: string | null;
  camera: string | null;
  caption: string | null;
}

const svgOrNull = (svg: string | undefined): string | null => (svg && svg.trim() ? svgDataUri(svg) : null);

export function inlineScene(s: VideoSceneView): InlineScene {
  const start = s.startFrame?.trim();
  const end = s.endFrame?.trim();
  return {
    key: `scene-${s.n}`,
    title: `장면 ${s.n} · ${secs(s.startS)}~${secs(s.endS)}초`,
    thumb: svgOrNull(s.startSvg),
    thumbEnd: svgOrNull(s.endSvg),
    frames: start || end ? `${start || "?"} → ${end || "?"}` : null,
    visual: s.visual,
    action: s.actionEn?.trim() || null,
    camera: s.cameraEn?.trim() || null,
    caption: s.caption?.trim() || null,
  };
}

export interface InlinePlanView {
  concept: string;
  meta: string; // "15초 · 9:16 · 장면 4"
  script: string | null;
  scenes: InlineScene[];
  /** 규칙 초안이면 그 이유 한 줄(필드가 있을 때만) */
  fallback: string | null;
  /** 3D 콘티를 못 그린 이유 한 줄(계약 VideoPlan.svg_reason, 기획 화면과 같은 문구) · 없으면 null */
  svgWhy: string | null;
  /** 콘티(SVG)를 못 그린 장면 수 · 0이면 줄을 두지 않는다 */
  missingSvg: number;
}

export function inlinePlan(v: VideoPlanView): InlinePlanView {
  const scenes = v.scenes.map(inlineScene);
  return {
    concept: v.concept,
    meta: `${v.durationS}초 · ${v.aspect} · 장면 ${v.scenes.length}`,
    script: v.script?.trim() || null,
    scenes,
    fallback: videoFallbackLine(v) || null,
    svgWhy: videoSvgLine(v) || null,
    missingSvg: scenes.filter((s) => !s.thumb).length,
  };
}

export const missingSvgKo = (n: number, total: number): string | null =>
  n > 0 ? (n === total ? "3D 콘티를 그리지 못해 장면 글만 보여요" : `3D 콘티가 없는 장면이 ${n}개 있어요`) : null;
