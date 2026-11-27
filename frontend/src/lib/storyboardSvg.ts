// SVG 콘티 보기의 순수 계산. node --test (storyboardSvg.test.ts)
// AI가 그린 SVG 글은 늘 <img src="data:image/svg+xml;base64,…">로만 보여 준다(스크립트가 돌지 않는다).
// 새 필드(board_source·shots[].svg)가 없으면 지금 화면(구도 그림 칸) 그대로다.
import type { StoryboardResult, StoryboardShot } from "./storyboard";

/** UTF-8 안전 base64 (한글 캡션이 깨지지 않게 TextEncoder → 바이트 → btoa) */
export function svgDataUri(svg: string): string {
  const bytes = new TextEncoder().encode(svg);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:image/svg+xml;base64,${btoa(bin)}`;
}

/** 스토리보드를 SVG 콘티로 보여 줄지: board_source가 "svg"이거나 칸 하나라도 svg가 있으면.
 *  11/27 운영 PROP-44: 준비 때 스토리보드 그림을 그리면 board_source가 "higgsfield"로 바뀌어, 칸에 기획 3D 콘티
 *  SVG가 있는데도 "구도 참고"가 빈 배치판(사람 모양)으로 나왔다. board_source는 참고만 한다.
 *  svg가 없는 칸은 SvgCellItem이 지금처럼 배치판으로 그린다 */
export const isSvgBoard = (r: Pick<StoryboardResult, "board_source"> & { shots?: Pick<StoryboardShot, "svg">[] }): boolean =>
  r.board_source === "svg" || (r.shots ?? []).some((s) => typeof s.svg === "string" && s.svg.trim() !== "");

export interface SvgCell {
  no: number;
  /** 칸 그림. null이면 지금 구도 그림(layout) 칸으로 */
  src: string | null;
  /** "0~2초" 같은 시간 글(시간이 없으면 null) */
  time: string | null;
  caption: string;
}

const secs = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));

/** 칸 하나: svg 글이 있으면 data URI, 없으면 null */
export function svgCell(shot: StoryboardShot): SvgCell {
  const svg = typeof shot.svg === "string" && shot.svg.trim() ? shot.svg : null;
  const time = shot.start_s != null && shot.end_s != null ? `${secs(shot.start_s)}~${secs(shot.end_s)}초` : null;
  return { no: shot.no, src: svg ? svgDataUri(svg) : null, time, caption: shot.caption_ko || shot.scene_ko };
}

/** [Higgsfield 그림으로 다시 그리기] 확인 단계 (브라우저 확인창 없이 화면 안에서) */
export type RedrawStep = "idle" | "confirm" | "running";
export type RedrawEvent = "click" | "ok" | "cancel" | "done";

/** 다시 그리기 전용 경로: POST …/creative-records/{id}/board-image (LLM 없이 그림만, 그림 크레딧) */
export const boardImagePath = (recordId: string): string =>
  `/api/dashboard/marketing/creative-records/${encodeURIComponent(recordId)}/board-image`;

/** 요청 실패 뒤: 404(서버에 아직 경로가 없거나 다른 회사 기록)면 버튼을 숨기고, 그 밖은 오류 문장을 보여 준다 */
export function redrawFailure(e: unknown): { hide: boolean; message: string | null } {
  const status = typeof e === "object" && e !== null && "status" in e ? (e as { status: unknown }).status : null;
  if (status === 404) return { hide: true, message: null };
  return { hide: false, message: e instanceof Error && e.message ? e.message : "그림을 요청하지 못했어요" };
}

export function redrawNext(step: RedrawStep, ev: RedrawEvent): RedrawStep {
  if (step === "idle" && ev === "click") return "confirm";
  if (step === "confirm" && ev === "ok") return "running";
  if (step === "confirm" && ev === "cancel") return "idle";
  if (step === "running" && ev === "done") return "idle";
  return step;
}
