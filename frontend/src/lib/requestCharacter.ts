// 홍보 영상 의뢰에 회사 캐릭터 붙이기 (11/23, 화면 시안 ①·②). 순수 계산만 한다: `npm test`(node --test)에서 그대로 돈다.
// - ① 고객 의뢰 넘김 카드: "회사 캐릭터나 아바타가 있나요?" [있어요][없어요] · 저장된 대표 캐릭터가 있으면 먼저 보여 준다
//   고른 결과는 총괄 의뢰 기록 brand_character_record_id (POST /api/requests/{id}/brand-character). 영상 준비 전까지만
// - ② 대화방 의뢰 진행 카드: [이미지 시트][스토리보드] (의뢰의 제작 기록 = 영상 준비 단계 결과 번호 CR-…), 영상 승인 대기면 [승인 화면 열기]
// 값은 서버가 준 것만 쓴다(지어내지 않는다).

import { isOpenRequest, type WorkRequestRow } from "./workRequests.ts";
import type { VideoPlanView } from "./videoPlan.ts";

export const PREPARE_SKILL = "prepare_video_draft_from_plan";
export const MAKE_SKILL = "generate_media";
export const VIDEO_APPROVAL = "video_approval";
export const CREATIVE_RECORD = /^CR-[0-9a-f]{12}$/;

// 시안 문구 그대로
export const CHARACTER_QUESTION = "영상에 쓸 회사 캐릭터나 아바타가 있나요?";
export const CHARACTER_NOTE = "캐릭터를 쓰면 영상을 만들기 전에 사람 승인을 한 번 받아요.";
export const RIGHTS_LABEL = "우리 회사가 쓸 권리가 있는 그림이에요";
export const RIGHTS_HINT = "(체크해야 올릴 수 있어요)";
export const UPLOAD_HINT = "PNG·JPG 한 장 · 사람 실사 사진은 그림체 캐릭터로 바꿔요";
export const SPACE_NOTE = "올린 이미지는 이 회사 공간에만 저장하고 이미지 시트(CR-…)를 만들어요. 다른 회사에는 보이지 않아요. 답하지 않으면 지금처럼 캐릭터 없이 진행해요.";
export const VIDEO_WAIT_LABEL = "영상 승인 대기";

export type CharacterRow = Pick<WorkRequestRow, "plan" | "steps" | "status"> & {
  /** 사람이 고른 대표 캐릭터(CR-…). null = 캐릭터 없이, 칸이 없으면 아직 답하지 않음 */
  brand_character_record_id?: string | null;
  stopped_at?: string | null;
};

export interface SavedCharacter {
  record_id: string;
  name: string;
  thumbnail_base64?: string | null;
  preview_part?: string | null;
  saved_at: string;
  is_default?: boolean;
}

const prepareStep = (r: Pick<WorkRequestRow, "plan">) => r.plan.find((p) => p.skill === PREPARE_SKILL);

/** 홍보 영상 흐름(영상 준비 단계가 있는 의뢰)인가 */
export const isVideoRequest = (r: Pick<WorkRequestRow, "plan">) => !!prepareStep(r);

/** 아직 캐릭터를 바꿀 수 있나: 열린 의뢰이고 영상 준비가 시작되지 않았다 (서버도 같은 규칙, 뒤면 409) */
export function characterOpen(r: CharacterRow): boolean {
  const step = prepareStep(r);
  if (!step || r.stopped_at || !isOpenRequest(r)) return false;
  return (r.steps[step.step_id]?.status ?? "pending") === "pending";
}

/** 질문 카드 모양 (시안 ①)
 *  - hidden: 홍보 영상 의뢰가 아니다
 *  - closed: 영상 준비가 시작됐다(고른 값만 보인다)
 *  - chosen: 캐릭터를 골랐다 · none: 캐릭터 없이로 답했다
 *  - saved: 아직 답하지 않았고 저장된 대표 캐릭터가 있다 → [이 캐릭터로 만들기][다른 이미지 올리기][캐릭터 없이]
 *  - ask: 아직 답하지 않았고 저장된 캐릭터가 없다 → [있어요][없어요] */
export type AskMode = "hidden" | "closed" | "chosen" | "none" | "saved" | "ask";

export function askMode(r: CharacterRow, saved: readonly SavedCharacter[] | null): AskMode {
  if (!isVideoRequest(r)) return "hidden";
  const answered = r.brand_character_record_id !== undefined;
  if (!characterOpen(r)) return "closed";
  if (answered) return r.brand_character_record_id ? "chosen" : "none";
  return saved && saved.length > 0 ? "saved" : "ask";
}

/** 올리기 버튼을 켤 수 있나: 이미지를 골랐고 권리 확인을 체크했다 (서버도 rights_confirmed 없으면 422) */
export const canUpload = (hasFile: boolean, rightsChecked: boolean, busy = false) => hasFile && rightsChecked && !busy;

/** 대표 캐릭터 한 줄 아래 설명: "이미지 시트 CR-… · 11/20 저장" */
export function savedLine(c: Pick<SavedCharacter, "record_id" | "saved_at">): string {
  const d = new Date(c.saved_at);
  const day = Number.isNaN(d.getTime()) ? "" : ` · ${d.getMonth() + 1}/${d.getDate()} 저장`;
  return `이미지 시트 ${c.record_id}${day}`;
}

/** 이 의뢰의 제작 기록(이미지 시트·스토리보드) = 영상 준비 단계 결과 번호. 없으면 null */
export function creativeRecordOf(r: Pick<WorkRequestRow, "plan" | "steps">): string | null {
  const step = prepareStep(r);
  const ref = step ? r.steps[step.step_id]?.result_ref : null;
  return ref && CREATIVE_RECORD.test(ref) ? ref : null;
}

/** 영상 승인 대기: 영상 제작 단계가 사람 승인을 기다린다(실패가 아니다). 승인은 승인 화면에서만 한다 */
export function videoApproval(r: Pick<WorkRequestRow, "plan" | "steps">): { stepId: string; reviewId: string | null } | null {
  for (const p of r.plan) {
    const s = r.steps[p.step_id];
    if (p.skill === MAKE_SKILL && s?.status === "held" && s.reason_code === VIDEO_APPROVAL) return { stepId: p.step_id, reviewId: s.review_id };
  }
  return null;
}

/** 진행 카드 버튼 (시안 ②): 제작 기록이 있으면 [이미지 시트][스토리보드], 없으면 기획 장면 글이 있을 때 [스토리보드]만 */
export function creativeButtons(r: Pick<WorkRequestRow, "plan" | "steps">, plan?: Pick<VideoPlanView, "scenes"> | null): { sheet: boolean; storyboard: boolean } {
  if (!isVideoRequest(r)) return { sheet: false, storyboard: false };
  const record = creativeRecordOf(r);
  return { sheet: !!record, storyboard: !!record || !!plan?.scenes.length };
}

/** 제작 기록이 없는 의뢰의 스토리보드: 기획 제작안 장면 글만 칸으로 (그림 없음) */
export const sceneCells = (plan: Pick<VideoPlanView, "scenes">) =>
  plan.scenes.map((s) => ({ key: s.n, title: `장면 ${s.n}`, text: s.visual, caption: s.caption }));
