// 대화방 "의뢰 진행" 카드(components/office/RequestProgressCard.tsx)의 순수 계산. node --test (requestCards.test.ts).
// 전체 대화방·이슈 방(useOfficeTalk)과 총괄 1:1(DirectChat)은 총괄 글 한 줄("의뢰를 접수했어요 (번호 wr_…)" · "[의뢰 wr_…] …")만 보였다.
// 의뢰 번호가 처음 나온 총괄 말풍선 아래에 카드 하나를 붙이고, 같은 의뢰의 다음 알림 줄("[의뢰 wr_…] …")은 그 방에서 숨긴다(원본 대화는 그대로).
import { STEP_STATUS, canDecide, type StepStatus, type WorkRequestPlanStep, type WorkRequestRow, type WorkRequestStep } from "./workRequests.ts";

const ID = "wr_[0-9a-f]{10}";
/** 총괄 코드가 만드는 문장 형식만: "(번호 wr_…)" · "[의뢰 wr_…]" */
const ID_IN_TEXT = new RegExp(`\\(번호 (${ID})\\b|\\[의뢰 (${ID})\\]`);
const NOTICE = new RegExp(`^\\s*\\[의뢰 (${ID})\\]`);

/** 총괄 글에서 의뢰 번호. 그 형식이 아니면(고객 글·다른 문장) null */
export function requestIdOf(text: string | null | undefined): string | null {
  const m = ID_IN_TEXT.exec(text ?? "");
  return m ? (m[1] ?? m[2]) : null;
}

/** 같은 의뢰의 진행 알림 줄 "[의뢰 wr_…] …"이면 그 번호 */
export const noticeIdOf = (text: string | null | undefined): string | null => NOTICE.exec(text ?? "")?.[1] ?? null;

export interface TalkLine { id: string; room: string; text: string; fromOrchestrator: boolean }

/**
 * 방마다: 의뢰 번호가 처음 나온 총괄 줄 id → 그 의뢰 번호(카드를 붙일 자리), 숨길 줄 id(같은 의뢰의 다음 알림 줄).
 * 사람 말(fromOrchestrator=false)에서는 찾지 않는다. 시각순으로 넘긴다
 */
export function placeRequestCards(lines: TalkLine[]): { cardAt: Map<string, string>; hidden: Set<string> } {
  const cardAt = new Map<string, string>();
  const hidden = new Set<string>();
  const seen = new Set<string>(); // room|request
  for (const l of lines) {
    if (!l.fromOrchestrator) continue;
    const rid = requestIdOf(l.text);
    if (!rid) continue;
    const key = `${l.room}|${rid}`;
    if (!seen.has(key)) {
      seen.add(key);
      cardAt.set(l.id, rid);
      continue;
    }
    if (noticeIdOf(l.text) === rid) hidden.add(l.id);
  }
  return { cardAt, hidden };
}

/** 카드를 그릴지: 사람 토큰이 있고 번호가 있을 때만 (없으면 지금 글만) */
export const showRequestCard = (hasToken: boolean, requestId: string | null | undefined) => hasToken && !!requestId;

// ---- 단계 줄

export type StepDot = "ok" | "run" | "warn" | "gray";
export interface StepRow {
  stepId: string;
  dept: string | null;
  label: string;
  skill: string;
  status: StepStatus;
  chip: { label: string; tone: string };
  dot: StepDot;
  /** 사람 확인 버튼 ("영상 제작 진행" · "여기서 멈추기"). 사람이 정할 단계가 아니면 null */
  decide: { approve: string; stop: string } | null;
  /** 실패·건너뜀이면 회색 + 의뢰 note(코드 문장) */
  muted: string | null;
}

const DOT: Record<StepStatus, StepDot> = {
  pending: "gray", running: "run", waiting_async: "run", completed: "ok", failed: "gray", skipped: "gray", rejected_by_human: "gray",
  held: "warn", waiting_human: "warn",
};

/** 다음 단계 label: 이 단계를 after로 가진 단계, 없으면 plan에서 바로 다음 */
function nextLabel(plan: WorkRequestPlanStep[], stepId: string): string | null {
  const after = plan.find((p) => p.after.includes(stepId));
  if (after) return after.label;
  const i = plan.findIndex((p) => p.step_id === stepId);
  return i >= 0 && i + 1 < plan.length ? plan[i + 1].label : null;
}

/** plan 순서대로 단계 줄 (칩 · 점 색 · 버튼 · 회색 note) */
export function stepRows(r: Pick<WorkRequestRow, "plan" | "steps">): StepRow[] {
  return r.plan.map((p) => {
    const s: WorkRequestStep | undefined = r.steps[p.step_id];
    const status: StepStatus = s?.status ?? "pending";
    const next = nextLabel(r.plan, p.step_id);
    const decide = s && canDecide(s) ? { approve: next ? `${next} 진행` : "진행", stop: "여기서 멈추기" } : null;
    const muted = status === "failed" || status === "skipped" ? (s?.note || STEP_STATUS[status].label) : null;
    return { stepId: p.step_id, dept: p.dept, label: p.label, skill: p.skill, status, chip: STEP_STATUS[status], dot: DOT[status], decide, muted };
  });
}

// ---- 보고서·기획 제안 잇기

/** 데이터 trend_report_create 단계의 보고서 id (result_ref가 tr_…일 때만) */
export function reportIdOfRequest(r: Pick<WorkRequestRow, "plan" | "steps">, stepId?: string): string | null {
  for (const p of r.plan) {
    if (p.skill !== "trend_report_create" || (stepId && p.step_id !== stepId)) continue;
    const ref = r.steps[p.step_id]?.result_ref ?? null;
    if (ref && /^tr_/.test(ref)) return ref;
  }
  return null;
}

/**
 * 기획 receive_insight 단계가 끝났으면 그 제안 번호: 단계 result_ref(PROP-…)가 먼저, 없으면 기획 기록 중 insightId가
 * "trend_report:<보고서 id>"인 것. 단계가 끝나지 않았으면 null
 */
export function proposalOfRequest(
  r: Pick<WorkRequestRow, "plan" | "steps">,
  proposals: { id: string; insightId?: string }[] | null | undefined,
): string | null {
  const p = r.plan.find((x) => x.skill === "receive_insight");
  const s = p ? r.steps[p.step_id] : undefined;
  if (!s || s.status !== "completed") return null;
  if (s.result_ref && /^PROP-/.test(s.result_ref)) return s.result_ref;
  const rep = reportIdOfRequest(r);
  if (!rep) return null;
  return (proposals ?? []).find((x) => x.insightId === `trend_report:${rep}`)?.id ?? null;
}
