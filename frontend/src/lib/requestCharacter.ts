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
// 11/24: 홍보 영상은 캐릭터 답을 받아야 데이터분석이 시작된다 ("답하지 않으면 캐릭터 없이"를 뺐다)
export const SPACE_NOTE = "올린 이미지는 이 회사 공간에만 저장하고 이미지 시트(CR-…)를 만들어요. 다른 회사에는 보이지 않아요. 답해야 다음 단계가 시작돼요. 영상 초안 준비 전까지는 바꿀 수 있어요.";
export const PLAN_OUTDATED_LINE = "기획서는 예전 캐릭터 기준이에요";
export const VIDEO_WAIT_LABEL = "영상 승인 대기";

export type CharacterRow = Pick<WorkRequestRow, "plan" | "steps" | "status"> & {
  /** 의뢰 종류. 없으면(옛 기록) 영상 준비 단계로만 본다 */
  task?: string | null;
  /** 사람이 고른 대표 캐릭터(CR-…). null = 캐릭터 없이, 칸이 없으면 아직 답하지 않음 */
  brand_character_record_id?: string | null;
  /** 11/24 캐릭터를 마지막으로 정한 때 (서버 기록) */
  brand_character_at?: string | null;
  stopped_at?: string | null;
};

// ---- 11/24 캐릭터 답을 먼저 받는다 (총괄 promo_video 맨 앞 사람 단계 character)
export const CHARACTER_SKILL = "brand_character";
const PLAN_STARTED: readonly string[] = ["running", "waiting_async", "completed"];

/** 캐릭터 단계(새 계획)가 있고 아직 답을 기다리나 (답 전에는 데이터분석이 시작되지 않는다) */
export function characterPending(r: CharacterRow): boolean {
  const step = r.plan.find((p) => p.skill === CHARACTER_SKILL);
  if (!step || r.stopped_at || !isOpenRequest(r)) return false;
  const status = r.steps[step.step_id]?.status ?? "pending";
  return status === "pending" || status === "waiting_human";
}

/** 기획이 시작된 뒤 캐릭터를 바꿨나 (서버 응답 plan_outdated와 같은 규칙: 기획 단계 시작 시각보다 나중에 정함) */
export function planOutdated(r: CharacterRow): boolean {
  const at = Date.parse(r.brand_character_at ?? "");
  if (!Number.isFinite(at)) return false;
  return r.plan.some((p) => {
    const s = r.steps[p.step_id];
    if (p.dept !== "planning" || !s || !PLAN_STARTED.includes(s.status)) return false;
    const started = Date.parse(s.started_at ?? "");
    return Number.isFinite(started) && at > started;
  });
}

export interface SavedCharacter {
  record_id: string;
  name: string;
  /** 11/24 올린 이미지 종류 (옛 서버·옛 기록은 없음 = real_photo) */
  kind?: "real_photo" | "virtual_human" | "illustration";
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

// 11/24 대화방 의뢰 진행 카드에도 같은 질문을 둔다 (#1636은 고객 의뢰 넘김 카드에만 있어 wr_6eace07d39가 질문 없이 진행됐다).
// 두 카드는 같은 의뢰 기록(brand_character_record_id)을 본다
export const PROMO_VIDEO_TASK = "promo_video";
export const CHAT_NONE_LINE = "캐릭터 없이 진행";
export const UNANSWERED_NOTICE = "회사 캐릭터를 정하지 않았어요. 이대로 진행하면 캐릭터 없이 만들어요.";

/** 홍보 영상 의뢰(promo_video)인가. 종류가 비어 있으면 영상 준비 단계가 있는지로 본다 */
export const isPromoVideo = (r: Pick<CharacterRow, "plan" | "task">) =>
  isVideoRequest(r) && (!r.task || r.task === PROMO_VIDEO_TASK);

/** 대화방 카드 맨 위에 질문을 띄우나: 홍보 영상 의뢰 · 영상 준비가 아직 pending · 캐릭터를 아직 정하지 않음 */
export const chatAskShown = (r: CharacterRow) =>
  isPromoVideo(r) && characterOpen(r) && r.brand_character_record_id === undefined;

/** 정한 뒤 접힌 한 줄: "회사 캐릭터: 이름(CR-…)" 또는 "캐릭터 없이 진행". [바꾸기]는 영상 준비 전까지만 (뒤면 서버도 409) */
export function chatCharacterLine(r: CharacterRow, name?: string | null): { text: string; canChange: boolean } | null {
  if (!isPromoVideo(r) || r.brand_character_record_id === undefined) return null;
  const id = r.brand_character_record_id;
  const text = id ? `회사 캐릭터: ${name ? `${name}(${id})` : id}` : CHAT_NONE_LINE;
  return { text, canChange: characterOpen(r) };
}

/** 사람 확인 단계 [영상 초안 준비 진행] 위 안내: 이 단계 다음이 영상 준비이고 캐릭터를 정하지 않았다. 막지는 않는다 */
export function unansweredNotice(r: CharacterRow, stepId: string): string | null {
  if (!chatAskShown(r)) return null;
  // 11/24 새 계획은 캐릭터 답 전에는 사람 확인까지 가지 않는다 ("캐릭터 없이 만들어요"는 옛 기록에만)
  if (r.plan.some((p) => p.skill === CHARACTER_SKILL)) return null;
  const i =r.plan.findIndex((p) => p.step_id === stepId);
  const next = r.plan.find((p) => p.after.includes(stepId)) ?? (i >= 0 ? r.plan[i + 1] : undefined);
  return next?.skill === PREPARE_SKILL ? UNANSWERED_NOTICE : null;
}

/** 올리기 버튼을 켤 수 있나: 이미지를 골랐고 권리 확인을 체크했다 (서버도 rights_confirmed 없으면 422) */
export const canUpload = (hasFile: boolean, rightsChecked: boolean, busy = false) => hasFile && rightsChecked && !busy;

// 11/24 올린 이미지 종류 (시안 ①). 서버 CharacterUploadKind와 같은 값
export type UploadKind = "real_photo" | "virtual_human" | "illustration";
export const KIND_OPTIONS: { kind: UploadKind; label: string; hint: string; isNew?: boolean }[] = [
  { kind: "real_photo", label: "실제 사람 사진", hint: "그림체 캐릭터로 바꿔서 써요(지금 방식). 실존 인물 얼굴을 광고에 쓰지 않기 위해서예요." },
  { kind: "virtual_human", label: "AI로 만든 가상 인물", hint: "올린 이미지를 그대로 참조로 써요. 다시 그리지 않아요(그림 크레딧 0).", isNew: true },
  { kind: "illustration", label: "그림·일러스트 캐릭터", hint: "그대로 써요." },
];
export const VIRTUAL_CONFIRM_LABEL = "실존 인물이 아닌 AI 가상 인물이고, 우리 회사가 쓸 권리가 있어요";
export const VIRTUAL_NOTE = "영상·게시물에 \"AI 가상 인물\" 표시가 붙어요.";
export const VIRTUAL_HUMAN_LABEL = "AI 가상 인물";
/** 원본을 그대로 참조로 쓰는 종류 (서버 REFERENCE_KINDS) */
export const isReferenceKind = (kind: UploadKind) => kind === "virtual_human" || kind === "illustration";

/** 올리기 버튼: 파일 + (가상 인물이면 가상 인물 확인, 아니면 권리 확인) */
export function canUploadKind(hasFile: boolean, kind: UploadKind, rightsChecked: boolean, virtualChecked: boolean, busy = false): boolean {
  return canUpload(hasFile, kind === "virtual_human" ? virtualChecked : rightsChecked, busy);
}

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
