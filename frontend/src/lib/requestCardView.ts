// 대화방 "의뢰 진행" 카드를 크게·한눈에 (11/26 재현 님 시안 ⓪ 접힘 · ① 진행 중 · ② 끝남). node --test (requestCardView.test.ts).
// 화면(components/office/RequestProgressCard.tsx)이 쓰는 순수 계산만 둔다. 값은 카드가 이미 받은 의뢰 기록·단계 줄(stepRows)만 쓴다(새 API 없음).
// - 머리 알약: 사람 확인 기다려요(주황) · 진행 중(브랜드) · ✓ 끝남(초록) · 멈춤·실패(빨강). 색만으로 말하지 않게 글자를 같이 둔다
// - 진행 막대: 단계 수만큼 칸(끝남 초록 · 지금 칸 주황/브랜드 · 남은 칸 회색) + "지금 N단계 / 7 · {지금 할 일}"
// - 단계 줄: 번호 원(✓/번호) · 부서 꼬리표 · 상태 글(완료·지금 할 일·진행 중·대기·실패·건너뜀) · 남은 단계는 무엇을 할지 한 줄

import { CHARACTER_SKILL, MAKE_SKILL, PREPARE_SKILL, VIDEO_APPROVAL, type CharacterRow } from "./requestCharacter.ts";
import type { StepRow } from "./requestCards.ts";
import type { RequestStatus } from "./workRequests.ts";

/** 칸·원·알약의 뜻 (색 토큰은 TONE_CLASS) */
export type CardTone = "wait" | "run" | "ok" | "bad" | "todo";

/** AXcellent 의미 토큰만 (tailwind.config.ts 색 = styles.css 변수) */
export const TONE_CLASS: Record<CardTone, { pill: string; bar: string; num: string; text: string; row: string }> = {
  wait: { pill: "bg-amber-50 text-amber-800", bar: "bg-amber-700", num: "bg-amber-700 text-surface", text: "text-amber-800", row: "bg-amber-50" },
  run: { pill: "bg-brand-50 text-brand-700", bar: "bg-brand-500", num: "bg-brand-600 text-surface", text: "text-brand-700", row: "bg-brand-50" },
  ok: { pill: "bg-emerald-50 text-emerald-800", bar: "bg-emerald-700", num: "bg-emerald-50 text-emerald-800", text: "text-emerald-800", row: "" },
  bad: { pill: "bg-red-50 text-red-800", bar: "bg-red-700", num: "bg-red-50 text-red-800", text: "text-red-800", row: "bg-red-50" },
  todo: { pill: "bg-ink-100 text-ink-700", bar: "bg-ink-300", num: "bg-ink-100 text-ink-500", text: "text-ink-500", row: "" },
};

const DONE: ReadonlySet<string> = new Set(["completed", "skipped"]);
const HUMAN_WAIT: ReadonlySet<string> = new Set(["waiting_human", "held"]);
const STOPPED: ReadonlySet<string> = new Set(["failed", "rejected_by_human"]);

/** 막대 아래 짧은 단계 이름 (홍보 영상 계획의 skill). 모르는 단계는 계획 이름 앞 5자 */
const SHORT: Record<string, string> = {
  [CHARACTER_SKILL]: "캐릭터", trend_report_create: "보고서", trend_report_insight: "인사이트", receive_insight: "제작안",
  human_check: "사람 확인", [PREPARE_SKILL]: "준비", [MAKE_SKILL]: "영상",
};
export const shortLabel = (s: Pick<StepRow, "skill" | "label">) => SHORT[s.skill] ?? s.label.slice(0, 5);

/** 부서 꼬리표: 데이터분석·기획·마케팅·사람 (부서가 없는 단계 = 사람이 정하는 단계). 글자색은 부서마다 다른 토큰 */
const DEPT: Record<string, { label: string; cls: string }> = {
  data: { label: "데이터분석", cls: "text-brand-700" },
  planning: { label: "기획", cls: "text-emerald-800" },
  marketing: { label: "마케팅", cls: "text-amber-800" },
  infra: { label: "인프라", cls: "text-brand-700" },
};
export function deptTag(dept: string | null): { label: string; cls: string } {
  if (!dept) return { label: "사람", cls: "text-ink-700" };
  return DEPT[dept] ?? { label: dept, cls: "text-ink-700" };
}

/** 남은 단계가 무엇을 할지 한 줄 (정적 문구). 모르는 단계는 null */
const FUTURE: Record<string, string> = {
  [CHARACTER_SKILL]: "영상에 쓸 회사 캐릭터를 정해요",
  trend_report_create: "트렌드·경쟁사를 분석해 보고서를 써요",
  trend_report_insight: "보고서에서 기획에 넘길 인사이트를 골라요",
  receive_insight: "인사이트로 영상 제작안을 써요",
  human_check: "제작안을 보고 진행할지 정해요",
  [PREPARE_SKILL]: "시트 · 대본 · 스토리보드 · 제작안 확인(필요하면 기획에 고치기 요청)",
  [MAKE_SKILL]: "영상 승인(1시간) 뒤 만들어요",
};
export const futureLine = (skill: string): string | null => FUTURE[skill] ?? null;

/** 지금 단계 = 계획 순서로 처음 끝나지 않은 단계 (끝난 의뢰면 null) */
export function currentIndex(rows: readonly Pick<StepRow, "status">[], status: RequestStatus): number | null {
  if (status === "done") return null;
  const i = rows.findIndex((s) => !DONE.has(s.status));
  return i < 0 ? null : i;
}

/** 지금 단계 줄의 뜻: 사람 차례(주황) · 실패(빨강) · 그 밖(돌고 있음, 브랜드) */
function currentTone(s: Pick<StepRow, "status">): CardTone {
  if (HUMAN_WAIT.has(s.status)) return "wait";
  if (STOPPED.has(s.status)) return "bad";
  return "run";
}

/** 막대 칸 (단계 수만큼) */
export function barCells(rows: readonly Pick<StepRow, "status">[], status: RequestStatus): CardTone[] {
  const cur = currentIndex(rows, status);
  return rows.map((s, i) => {
    if (DONE.has(s.status)) return "ok";
    if (i === cur) return currentTone(s);
    if (STOPPED.has(s.status)) return "bad";
    return "todo";
  });
}

/** 머리 알약 */
export function headPill(rows: readonly Pick<StepRow, "status">[], status: RequestStatus): { label: string; tone: CardTone } {
  if (status === "done") return { label: "✓ 끝남", tone: "ok" };
  if (status === "failed" || status === "rejected") return { label: "멈춤·실패", tone: "bad" };
  if (status === "waiting" || status === "needs_human" || rows.some((s) => HUMAN_WAIT.has(s.status))) return { label: "● 사람 확인 기다려요", tone: "wait" };
  return { label: "진행 중", tone: "run" };
}

type NowStep = Pick<StepRow, "status" | "label" | "dept" | "decide" | "chip"> & { reasonCode?: string | null };

/** 지금 할 일 한 줄 (사람 차례면 무엇을 눌러야 하는지, 돌고 있으면 누가 하는지) */
export function todoLine(s: NowStep): string {
  if (s.chip.label === "답 대기") return "회사 캐릭터를 정해 주세요";
  if (s.reasonCode === VIDEO_APPROVAL) return "영상 승인을 기다려요 · 승인 화면에서 승인해 주세요";
  if (s.chip.label === "재시작으로 멈춤") return "멈춘 단계를 다시 보낼지 정해 주세요";
  if (s.decide) return `${s.label} · 진행할지 정해 주세요`;
  if (s.status === "held") return `${s.label} · 승인을 기다려요`;
  if (s.status === "failed") return `${s.label}에서 멈췄어요`;
  if (s.status === "rejected_by_human") return `${s.label}에서 멈췄어요 (사람이 멈춤)`;
  if (s.status === "pending") return `${s.label} · 곧 시작해요`;
  return `${deptTag(s.dept).label} · ${s.label} 하는 중`;
}

/** "지금 5단계 / 7 · 제작안 확인 · 진행할지 정해 주세요". 끝나면 "✓ 영상 완성"(영상 의뢰) 또는 "✓ 모두 끝났어요" */
/** short: 접힌 카드 한 줄 "지금 5/7 · …" · todo: 지금 단계 줄 안 할 일 문장 */
export function nowLine(rows: readonly NowStep[], status: RequestStatus, video: boolean): { text: string; short: string; todo: string | null; tone: CardTone; step: number | null } {
  const total = rows.length;
  const cur = currentIndex(rows, status);
  if (cur === null) {
    const text = status === "done" || total === 0 ? (video ? "✓ 영상 완성" : "✓ 모두 끝났어요") : "진행을 멈췄어요";
    return { text, short: text, todo: null, tone: status === "done" || total === 0 ? "ok" : "bad", step: null };
  }
  const s = rows[cur];
  const todo = todoLine(s);
  return { text: `지금 ${cur + 1}단계 / ${total} · ${todo}`, short: `지금 ${cur + 1}/${total} · ${todo}`, todo, tone: currentTone(s), step: cur + 1 };
}

/** 지금 단계 줄의 색 (nowLine과 같은 규칙) */
export const toneOfCurrent = (s: Pick<StepRow, "status">): CardTone => currentTone(s);

/** 고른 회사 캐릭터 번호 (없으면 null: 안 골랐거나 캐릭터 없이) */
export const brandIdOf = (r: CharacterRow): string | null => r.brand_character_record_id ?? null;

/** 단계 줄 상태 글 · 원 (current: 지금 단계인지) */
export function stepState(s: Pick<StepRow, "status" | "skill">, current: boolean): { text: string; tone: CardTone } {
  if (s.status === "completed") return { text: s.skill === "human_check" ? "진행함" : "완료", tone: "ok" };
  if (s.status === "skipped") return { text: "건너뜀", tone: "todo" };
  if (s.status === "failed") return { text: "실패", tone: "bad" };
  if (s.status === "rejected_by_human") return { text: "멈춤", tone: "bad" };
  if (HUMAN_WAIT.has(s.status)) return { text: "지금 할 일", tone: "wait" };
  if (s.status === "running" || s.status === "waiting_async") return { text: "진행 중", tone: "run" };
  return current ? { text: "곧 시작", tone: "run" } : { text: "대기", tone: "todo" };
}

/** 접힌 카드 설명 2줄: 회사 캐릭터 줄 → 제작안 콘셉트 → 의뢰 원문 순서로 처음 있는 것 */
export function peekText(character: string | null | undefined, concept: string | null | undefined, request: string | null | undefined): string | null {
  for (const t of [character, concept, request]) if (t && t.trim()) return t.trim();
  return null;
}

/** 이미지 시트·스토리보드·승인·기술 검사 칸(RequestCreative)을 둘 자리: 끝났으면 머리 영상 카드, 아니면 영상 단계(시작했으면) → 준비 단계 → 맨 아래 */
export function creativeSpot(rows: readonly Pick<StepRow, "skill" | "status" | "stepId">[], status: RequestStatus, hasVideo: boolean): string {
  if (status === "done" && hasVideo) return "head";
  const make = rows.find((s) => s.skill === MAKE_SKILL);
  if (make && make.status !== "pending") return make.stepId;
  const prep = rows.find((s) => s.skill === PREPARE_SKILL);
  if (prep && prep.status !== "pending") return prep.stepId;
  return "bottom";
}
