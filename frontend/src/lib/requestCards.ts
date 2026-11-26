// 대화방 "의뢰 진행" 카드(components/office/RequestProgressCard.tsx)의 순수 계산. node --test (requestCards.test.ts).
// 전체 대화방·이슈 방(useOfficeTalk)과 총괄 1:1(DirectChat)은 총괄 글 한 줄("의뢰를 접수했어요 (번호 wr_…)" · "[의뢰 wr_…] …")만 보였다.
// 의뢰 번호가 처음 나온 총괄 말풍선 아래에 카드 하나를 붙이고, 같은 의뢰의 다음 알림 줄("[의뢰 wr_…] …")은 그 방에서 숨긴다(원본 대화는 그대로).
import { CHARACTER_WAIT_CHIP, STEP_STATUS, canDecide, isCharacterWait, isRestartStuck, requestContext, type StepStatus, type WorkRequestPlanStep, type WorkRequestRow, type WorkRequestStep } from "./workRequests.ts";
import { planId } from "./format.ts";
import { planDocPath } from "./planDoc.ts";
import { videoPlanSummary, type VideoPlanView } from "./videoPlan.ts";

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

/** 단톡방 대화 말풍선 id. 서버 메시지 id(m1·m2…)는 대화마다 다시 시작해서 대화 id를 같이 넣는다 (11/24 ①: 의뢰 4개의 접수 말풍선 id가 같았다) */
export const directItemId = (agent: string, threadId: string, messageId: string) => `direct:${agent}:${threadId}:${messageId}`;

export interface TalkLine { id: string; room: string; text: string; fromOrchestrator: boolean }

/**
 * 방마다: 의뢰 번호가 처음 나온 총괄 줄 id → 그 의뢰 번호(카드를 붙일 자리), 숨길 줄 id(같은 의뢰의 다음 알림 줄).
 * 사람 말(fromOrchestrator=false)에서는 찾지 않는다. 시각순으로 넘긴다
 */
export function placeRequestCards(lines: TalkLine[]): {
  cardAt: Map<string, string>; hidden: Set<string>;
  /** 줄 순서 그대로: i번째 줄에 붙일 카드 번호(없으면 null)·숨길지. 줄 id가 겹쳐도(대화마다 m1·m2…) 섞이지 않는다 (11/24 ①) */
  cards: (string | null)[]; skip: boolean[];
} {
  const cardAt = new Map<string, string>();
  const hidden = new Set<string>();
  const cards: (string | null)[] = lines.map(() => null);
  const skip: boolean[] = lines.map(() => false);
  const seen = new Set<string>(); // room|request
  lines.forEach((l, i) => {
    if (!l.fromOrchestrator) return;
    const rid = requestIdOf(l.text);
    if (!rid) return;
    const key = `${l.room}|${rid}`;
    if (!seen.has(key)) {
      seen.add(key);
      cardAt.set(l.id, rid);
      cards[i] = rid;
      return;
    }
    if (noticeIdOf(l.text) === rid) {
      hidden.add(l.id);
      skip[i] = true;
    }
  });
  return { cardAt, hidden, cards, skip };
}

/**
 * 항목 목록에 카드를 붙이고 같은 의뢰의 다음 알림 줄을 뺀다. 줄 위치로 고르므로 항목 id가 겹쳐도 각자 번호다.
 * line: 카드 후보 줄이면 TalkLine, 아니면 null(말풍선이 아닌 항목). put: 그 항목에 카드 번호를 단다
 */
export function attachRequestCards<T>(items: T[], line: (it: T) => TalkLine | null, put: (it: T, requestId: string) => T): T[] {
  const idx: number[] = [];
  const lines: TalkLine[] = [];
  items.forEach((it, i) => {
    const l = line(it);
    if (l) { idx.push(i); lines.push(l); }
  });
  const { cards, skip } = placeRequestCards(lines);
  const card = new Map<number, string>();
  const drop = new Set<number>();
  idx.forEach((at, k) => {
    if (cards[k]) card.set(at, cards[k] as string);
    if (skip[k]) drop.add(at);
  });
  if (card.size === 0 && drop.size === 0) return items;
  return items.flatMap((it, i) => (drop.has(i) ? [] : [card.has(i) ? put(it, card.get(i) as string) : it]));
}

/** 카드를 그릴지: 사람 토큰이 있고 번호가 있을 때만 (없으면 지금 글만) */
export const showRequestCard = (hasToken: boolean, requestId: string | null | undefined) => hasToken && !!requestId;

/** 삭제한 의뢰(hidden_at)의 접힌 한 줄: 카드 대신 "삭제한 의뢰 · <제목> (<번호>)" + 사람 토큰이 있으면 [되돌리기]. 숨기지 않았으면 null(카드 전체) */
export function hiddenRequestLine(row: { request_id: string; hidden_at?: string | null }, title: string, hasToken: boolean): { text: string; canUndo: boolean } | null {
  if (!row.hidden_at) return null;
  return { text: `삭제한 의뢰 · ${title} (${row.request_id})`, canUndo: hasToken };
}

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
  /** 버튼 위 안내 한 줄 (재시작으로 멈춘 단계) */
  notice?: string;
}

export const RESTART_CHIP = { label: "재시작으로 멈춤", tone: "bg-amber-50 text-amber-800" };
export const RESTART_APPROVE = "다시 보내기";
export const RESTART_NOTICE = "서버가 다시 켜지면서 이 단계를 보냈는지 몰라 멈췄어요. 같은 일이 두 번 될 수 있으니 담당 부서 화면에서 이미 됐는지 보고 눌러 주세요.";

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
    const restart = !!s && isRestartStuck(s);
    // 재시작으로 멈춘 단계(11/24 ③): 승인 = 같은 단계를 다시 보낸다, 거절 = 여기서 멈춤 (같은 decideRequestStep)
    const decide = s && canDecide(s)
      ? { approve: restart ? RESTART_APPROVE : next ? `${next} 진행` : "진행", stop: "여기서 멈추기" }
      : null;
    const muted = status === "failed" || status === "skipped" ? (stepNoteText(s?.note) || STEP_STATUS[status].label) : null;
    return {
      stepId: p.step_id, dept: p.dept, label: p.label, skill: p.skill, status,
      // 11/24 회사 캐릭터 답 대기: 버튼은 카드 맨 위 질문 상자에만 있다 (승인·거절 아님)
      chip: restart ? RESTART_CHIP : s && isCharacterWait(s) ? CHARACTER_WAIT_CHIP : STEP_STATUS[status], dot: DOT[status], decide, muted,
      ...(restart ? { notice: RESTART_NOTICE } : {}),
    };
  });
}

// ---- 보고서·기획 제안 잇기

/** 재무 closing_report 단계가 끝났으면 그 결산 기간 (result_ref "<day|week|month>:<YYYY-MM-DD>", 11/26). 아니면 null */
export function closingOfRequest(r: Pick<WorkRequestRow, "plan" | "steps">, stepId?: string): { period: "day" | "week" | "month"; date: string } | null {
  for (const p of r.plan) {
    if (p.skill !== "closing_report" || (stepId && p.step_id !== stepId)) continue;
    const s = r.steps[p.step_id];
    const m = s?.status === "completed" ? /^(day|week|month):(\d{4}-\d{2}-\d{2})$/.exec(s.result_ref ?? "") : null;
    if (m) return { period: m[1] as "day" | "week" | "month", date: m[2] };
  }
  return null;
}

/** 인프라 의뢰(11/26 총괄 대화방): 끝난 단계의 결과 번호를 읽는다. incidents:<N|unknown> · deploys:<N> · 진단 status. 모르는 모양이면 null */
export type InfraResult =
  | { kind: "incidents"; count: number | null }
  | { kind: "deploys"; count: number }
  | { kind: "diagnosis"; status: "suspected" | "undetermined" };
export function infraOfRequest(r: Pick<WorkRequestRow, "plan" | "steps">, stepId?: string): InfraResult | null {
  for (const p of r.plan) {
    if (p.dept !== "infra" || (stepId && p.step_id !== stepId)) continue;
    const s = r.steps[p.step_id];
    if (s?.status !== "completed") continue;
    const ref = s.result_ref ?? "";
    if (p.skill === "current_incidents") {
      const m = /^incidents:(\d+|unknown)$/.exec(ref);
      if (m) return { kind: "incidents", count: m[1] === "unknown" ? null : Number(m[1]) };
    } else if (p.skill === "latest_deployments") {
      const m = /^deploys:(\d+)$/.exec(ref);
      if (m) return { kind: "deploys", count: Number(m[1]) };
    } else if (p.skill === "diagnose_incident" && (ref === "suspected" || ref === "undetermined")) {
      return { kind: "diagnosis", status: ref };
    }
  }
  return null;
}

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

// ---- 기획 단계 칸 (영상 제작안 요약 · 기획서 열기 · PDF로 저장)

/** 기획 기록에서 읽은 제안 하나 (카드가 쓰는 칸만) */
export interface PlanLookup {
  id: string;
  insightId?: string;
  spec?: string;
  title: string;
  idea: string;
  videoPlan?: Pick<VideoPlanView, "concept" | "durationS" | "aspect" | "scenes">;
  /** 11/26 카드 안 [펼쳐 보기]용 영상 제작안 전체(같은 기획 기록, 새 API 없음) */
  fullPlan?: VideoPlanView;
  /** 기획이 받은 성과 (receive_campaign_update result) · 그 캠페인 번호 (의뢰 영상이면 wr_…) */
  result?: { direction: string; summary?: string };
  campaign?: string;
}

export interface PlanCardView {
  /** 단계 칩: 기획서가 있으면 "기획서 N", 없으면 "제안 N" (idText 규칙) */
  chip: string;
  /** "영상 제작안 · 15초 · 9:16 · 장면 4" · 영상 제작안이 없으면 "기획 제안" */
  head: string;
  /** 콘셉트 한 줄 · 없으면 할 일(idea) */
  line: string;
  openPath: string;
  printPath: string;
}

/** request: 이 카드의 의뢰 번호. 열기 주소에 ?request=로 붙어 기획 화면 아래에서 사람 확인을 할 수 있다 */
export function planCard(p: PlanLookup, request?: string | null): PlanCardView {
  const v = p.videoPlan;
  return {
    chip: planId(p.spec || p.id),
    head: v ? videoPlanSummary(v) : "기획 제안",
    line: (v ? v.concept : p.idea) || p.title,
    openPath: planDocPath({ id: p.id, spec: p.spec, request }),
    printPath: planDocPath({ id: p.id, spec: p.spec }, true),
  };
}

/** 기획 기록을 다시 읽어도 되나: 처음이거나 마지막 읽기에서 1분이 지났을 때만 */
export const PLAN_LOOKUP_MIN_MS = 60_000;
export const planLookupDue = (lastAt: number | null, now: number) => lastAt === null || now - lastAt >= PLAN_LOOKUP_MIN_MS;

// 11/23 기획서가 늦게·안 뜨는 문제: 카드가 먼저 뜰 때 읽은 목록에 새 제안이 없으면 1분 제한 때문에 계속 비어 있었다.
// - 기획 단계 결과(result_ref)가 PROP-…인데 기억해 둔 목록에 없으면 1분 제한 없이 바로 다시 읽는다(같은 번호는 10초에 한 번까지)
// - receive_insight가 끝났는데 제안을 못 찾았으면 15초 간격으로 최대 8번 다시 읽는다(카드가 사라지면 멈춤)
export const PLAN_REFETCH_MIN_MS = 10_000;
export const PLAN_RETRY_MS = 15_000;
export const PLAN_RETRY_MAX = 8;

/** 기획 단계(receive_insight)가 끝나 결과 번호가 PROP-…면 그 번호, 아니면 null */
function planResultRef(r: Pick<WorkRequestRow, "plan" | "steps">): string | null {
  const p = r.plan.find((x) => x.skill === "receive_insight");
  const s = p ? r.steps[p.step_id] : undefined;
  return s?.status === "completed" && s.result_ref && /^PROP-/.test(s.result_ref) ? s.result_ref : null;
}

/** 1분 제한을 무시하고 기획 목록을 다시 읽을까: 결과 번호(PROP-)가 목록에 없고, 그 번호로 10초 안에 읽은 적이 없을 때 */
export function needPlanRefetch(
  r: Pick<WorkRequestRow, "plan" | "steps">,
  list: readonly { id: string }[] | null | undefined,
  lastAskedAt: number | null,
  now: number,
): boolean {
  const id = planResultRef(r);
  if (!id || list?.some((p) => p.id === id)) return false;
  return lastAskedAt === null || now - lastAskedAt >= PLAN_REFETCH_MIN_MS;
}

/** 기획 단계가 끝났고 그 제안이 목록에 있나 (없으면 15초 간격 다시 읽기를 이어 간다) */
export function planFound(r: Pick<WorkRequestRow, "plan" | "steps">, list: readonly { id: string; insightId?: string }[] | null | undefined): boolean {
  const id = proposalOfRequest(r, list as { id: string; insightId?: string }[] | null | undefined);
  return !!id && !!list?.some((p) => p.id === id);
}

// ---- 의뢰 영상 성과 (데이터 video_performance campaign_id = 의뢰 번호) · 기획 결과 받기 (11/23)
// 영상 제작(generate_media)이 끝난 의뢰만 1분에 한 번까지 읽는다 (planLookupDue와 같은 간격).
// 등록 안 됐으면(영상 id 없음) 아무 줄도 더하지 않는다

const CP_NAME: Record<string, string> = { "1h": "1시간", "1d": "1일", "3d": "3일", "7d": "7일" };
const CP_ORDER = ["1h", "1d", "3d", "7d"] as const;
export const SALES_UNMEASURED = "매출은 프로모션 링크가 없어 측정 불가";

/** 영상 제작 단계가 끝났나 (성과를 읽을 때) */
export function madeVideo(r: Pick<WorkRequestRow, "plan" | "steps">): boolean {
  const p = r.plan.find((x) => x.skill === "generate_media");
  return !!(p && r.steps[p.step_id]?.status === "completed");
}

export interface PerfCheckpointLike {
  checkpoint: string;
  status: "known" | "unknown";
  reason?: string | null;
  due_at?: string | null;
  views?: number | null;
  likes?: number | null;
  peer_ratio?: number | null;
}
export interface PerfLike {
  status: "known" | "unknown";
  reason?: string | null;
  video_id?: string | null;
  platform?: string | null;
  published_at?: string | null;
  checkpoints: PerfCheckpointLike[];
  peer_basis?: string | null;
}
export interface PerfCell { name: string; main: string; sub: string | null; wait: boolean }
export interface PerfStepView {
  chip: { label: string; tone: string };
  cells: PerfCell[];
  /** "인스타 @계정 릴스 · 공식 API · 매출은 프로모션 링크가 없어 측정 불가" */
  source: string;
  /** 아무 시점도 못 쟀을 때의 서버 이유 (게시 계정 없음·못 찾음 등). 재는 중이면 null */
  note: string | null;
  /** 7일을 쟀거나 놓쳤다 (기획에 결과를 보낼 차례) */
  done: boolean;
}

const n = (v: number) => v.toLocaleString("ko-KR");

/** "11/29" (한국 날짜) */
function kstDay(iso: string): string {
  const t = new Date(new Date(iso).getTime() + 9 * 3600_000);
  return `${t.getUTCMonth() + 1}/${t.getUTCDate()}`;
}

function cellOf(c: PerfCheckpointLike | undefined, name: string, lowSample: boolean): PerfCell {
  if (c && c.status === "known" && typeof c.views === "number") {
    const main = `조회 ${n(c.views)}` + (typeof c.likes === "number" ? ` · 좋아요 ${n(c.likes)}` : "");
    const sub = typeof c.peer_ratio === "number" ? `우리 릴스 중앙값의 ${c.peer_ratio.toFixed(1)}배` : lowSample ? "비교 표본 적음" : null;
    return { name, main, sub, wait: false };
  }
  const why = (c?.reason ?? "").trim();
  if (why.startsWith("놓침")) return { name, main: "놓침", sub: null, wait: true };
  if (c?.due_at && (!why || why.startsWith("측정 중"))) return { name, main: `${kstDay(c.due_at)} 잼`, sub: null, wait: true };
  return { name, main: "측정 중", sub: null, wait: true };
}

/** 성과 측정 단계 칸. 등록 안 됐으면(영상 id 없음) null */
export function perfStep(out: PerfLike | null | undefined, now: number): PerfStepView | null {
  if (!out || !out.video_id) return null;
  const by = new Map(out.checkpoints.map((c) => [c.checkpoint, c]));
  const lowSample = /low_sample/.test(out.peer_basis ?? "");
  const cells = CP_ORDER.map((k) => cellOf(by.get(k), CP_NAME[k], lowSample));
  const known = out.checkpoints.filter((c) => c.status === "known");
  const seven = by.get("7d");
  const done = seven?.status === "known" || (seven?.reason ?? "").startsWith("놓침");
  let chip: PerfStepView["chip"];
  if (done) chip = { label: "측정 끝", tone: "bg-emerald-50 text-emerald-800" };
  else if (!known.length && out.status === "unknown" && out.reason && !out.reason.startsWith("측정 중")) chip = { label: "확인 필요", tone: "bg-amber-50 text-amber-800" };
  else if (out.published_at) {
    const days = Math.max(1, Math.ceil((now - new Date(out.published_at).getTime()) / 86_400_000));
    chip = { label: `${days}일째`, tone: "bg-sky-50 text-sky-800" };
  } else chip = { label: "측정 준비", tone: "bg-sky-50 text-sky-800" };
  const account = /@[A-Za-z0-9._]+/.exec(out.peer_basis ?? "")?.[0];
  const where = out.platform === "instagram" ? `인스타 ${account ? `${account} ` : ""}릴스` : (out.platform ?? "영상");
  const note = !known.length && out.reason && !out.reason.startsWith("측정 중") ? out.reason : null;
  return { chip, cells, source: `${where} · 공식 API · ${SALES_UNMEASURED}`, note, done };
}

export interface ResultStepView { chip: { label: string; tone: string }; line: string }

/** 기획 결과 받기 줄: 이 의뢰(handoff.campaign)로 결과를 받은 제안이면 "제안 N · 늘었음", 아니면 7일 뒤 대기 */
export function resultStep(
  requestId: string,
  proposal: { id: string; spec?: string; campaign?: string; result?: { direction: string; summary?: string } } | null | undefined,
  perf: PerfStepView,
): ResultStepView {
  if (proposal?.result && proposal.campaign === requestId) {
    const head = `${planId(proposal.id)} · ${proposal.result.direction}`;
    return { chip: { label: "결과 받음", tone: "bg-emerald-50 text-emerald-800" }, line: proposal.result.summary ? `${head} · ${proposal.result.summary}` : head };
  }
  return perf.done
    ? { chip: { label: "보내는 중", tone: "bg-sky-50 text-sky-800" }, line: "7일 성과를 기획 제안에 보내는 중이에요" }
    : { chip: { label: "7일 뒤", tone: "bg-ink-100 text-ink-700" }, line: "7일을 재면 기획 제안에 결과를 보내요 · 다음 분석 의뢰의 \"지난번 결과\"에 들어가요" };
}

// ---- 버튼 실패 문구 (사람 확인 결정 · 다시 돌리기)
// 11/20 15:21: decideRequestStep이 nginx 403으로 실패했는데 `.catch(() => {})`로 넘겨 버튼이 먹통처럼 보였다

/** 의뢰 버튼 실패 → 버튼 아래 한 줄. reload: 목록을 다시 읽어야 하는지 (이미 처리됨) */
export function requestActionError(e: unknown): { text: string; reload: boolean } {
  const status = typeof (e as { status?: unknown } | null)?.status === "number" ? (e as { status: number }).status : null;
  if (status === 403) return { text: "서버가 막았어요 (403) · 관리자에게 이 경로 허용을 요청해 주세요", reload: false };
  if (status === 409) return { text: "이미 다른 사람이 처리했어요", reload: true };
  if (status === 404) return { text: "의뢰나 단계를 찾지 못했어요", reload: false };
  return { text: status === null ? "처리하지 못했어요" : `처리하지 못했어요 (${status})`, reload: false };
}

/** 의뢰 삭제·수정·되돌리기 실패 → 한 줄. 403·404·409는 requestActionError 그대로,
 *  422·502·503은 서버가 준 문장("바뀐 내용이 없어요"·"지금 의뢰는 그대로예요" 등)을 쓴다 */
export function requestEditError(e: unknown): { text: string; reload: boolean } {
  const status = typeof (e as { status?: unknown } | null)?.status === "number" ? (e as { status: number }).status : null;
  const message = e instanceof Error ? e.message : "";
  // 422(같은 글·같은 의뢰가 이미 있음·접수할 수 없는 의뢰)·429(하루 접수 한도)·502·503: 서버 문장 그대로, 수정 칸은 열어 둔다.
  // 409는 결정 실패 문구(이미 처리됨, 목록 다시 읽기) 그대로다
  if (status !== null && [422, 429, 502, 503].includes(status) && message) return { text: message, reload: false };
  return requestActionError(e);
}

// ---- 의뢰 진행 카드가 보이는 방 (작업 기록 접기 ①의 조건, lib/commFilter.ts chatLane)

/**
 * 이 방에 의뢰 진행 카드가 실제로 보이는 의뢰의 context(ctx_wr_…): 방의 총괄 말풍선에 의뢰 번호가 있고(placeRequestCards와 같은 규칙),
 * 그 의뢰가 의뢰 목록에 있고, 사람 토큰이 있을 때만. 아니면 빈 집합 (그 의뢰 줄은 대화로 남긴다 — 흐름이 어디에도 안 보이는 일이 없게)
 */
export function cardContextsIn(
  lines: { text: string; fromOrchestrator: boolean }[],
  requests: readonly Pick<WorkRequestRow, "request_id" | "context_id">[],
  hasToken: boolean,
): Set<string> {
  const out = new Set<string>();
  if (!hasToken || !requests.length) return out;
  const ids = new Set(lines.filter((l) => l.fromOrchestrator).map((l) => requestIdOf(l.text)).filter((x): x is string => !!x));
  for (const r of requests) if (ids.has(r.request_id)) out.add(requestContext(r));
  return out;
}

// ---- 단계 실패 문구

/** 단계 note → 카드에 보일 문구. "failed: in_progress"·timeout 계열은 "응답 대기 시간 초과", 그 밖 코드 문장은 그대로 */
export function stepNoteText(note: string | null | undefined): string {
  const n = (note ?? "").trim();
  if (/^failed:\s*in_progress\b/i.test(n) || /time[d\s_-]*out|deadline[_\s-]*exceeded|시간\s*초과/i.test(n)) return "응답 대기 시간 초과";
  return n;
}
