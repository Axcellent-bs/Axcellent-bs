import { ALL_ROOM, deptOf, skillKo, speaker, type ChatItem, type OrchIncident, type OrchStep } from "./agentChat";

// AI 오피스: AI 대화방과 같은 기록(총괄 사건·안전 관문·공통 로그)을 픽셀 사무실 상태로 바꾼다.
// 상태는 기록된 값(단계 status·말풍선 문장·facts)으로만 정하고, 화면 없는 순수 함수만 둔다.

// ---------------------------------------------------------------- 자리
export type DeskKey = "infra" | "data" | "support" | "finance" | "planning" | "marketing" | "hr" | "dev" | "orchestrator";

export interface DeskDef { key: DeskKey; label: string; x: number; y: number; shirt: string; hair: string; skin: string }

/** 사무실 좌표(논리 픽셀 256×176). 색은 캔버스 픽셀 그림 전용이다 (화면 UI 색은 디자인 토큰을 쓴다) */
export const DESKS: DeskDef[] = [
  { key: "infra", label: "인프라", x: 14, y: 62, shirt: "#2a9d8f", hair: "#1b1b1b", skin: "#f2c9a0" },
  { key: "data", label: "데이터분석", x: 74, y: 62, shirt: "#6d28d9", hair: "#2b1d14", skin: "#f5d0b0" },
  { key: "support", label: "고객지원", x: 134, y: 62, shirt: "#047857", hair: "#6b3e26", skin: "#f2c9a0" },
  { key: "finance", label: "재무", x: 194, y: 62, shirt: "#b45309", hair: "#3a2a1a", skin: "#e8b98f" },
  { key: "planning", label: "기획", x: 14, y: 128, shirt: "#5b4fd0", hair: "#191919", skin: "#f5d0b0" },
  { key: "marketing", label: "마케팅", x: 74, y: 128, shirt: "#ec7452", hair: "#7a4a2a", skin: "#f2c9a0" },
  { key: "hr", label: "인사", x: 134, y: 128, shirt: "#b91c1c", hair: "#4a2f1b", skin: "#e8b98f" },
  // 개발: 기획 맞은편 의자(뒷모습). x·y는 쓰는 곳이 없어(그림 자리는 PixelOffice SEATS) 기획 아래쪽 값만 둔다
  { key: "dev", label: "개발", x: 14, y: 150, shirt: "#0369a1", hair: "#3a2a1a", skin: "#f2c9a0" },
  { key: "orchestrator", label: "총괄", x: 194, y: 128, shirt: "#2e2688", hair: "#222222", skin: "#f5d0b0" },
];

/** 기록의 부서 이름 → 자리. 회사 기억(memory)은 데이터 부서 담당이라 데이터 자리에 앉힌다 */
export function deskOf(name: string | null | undefined): DeskKey | null {
  if (!name) return null;
  // 서비스 이름(inquiry·coupon·org 등)은 먼저 부서로 (AI 대화방 이름과 같은 규칙)
  const dept = deptOf(name);
  if (dept === "memory") return "data";
  if (dept === "safety") return "finance";
  return DESKS.some((d) => d.key === dept) ? (dept as DeskKey) : null;
}

// ---------------------------------------------------------------- 자리 상태
export type DeskState = "idle" | "working" | "waiting";
export type BubbleKind = "say" | "alert" | "think" | "ok" | "warn";
export interface Bubble { text: string; kind: BubbleKind }
export interface DeskView { state: DeskState; bubble: Bubble | null; skill: string | null }
export type OfficeStatus = "ok" | "alert" | "resolved" | "degraded";

/**
 * 걸어가기 한 번. `who`가 자리에서 `to`(총괄 앞 만남 자리, 또는 다른 부서 옆 통로)로 걸어가 잠깐 서 있다가 돌아온다.
 * `id`는 그 기록 줄 id, `at`은 화면이 처음 본 시각(ms). `say`는 도착해서 띄울 말
 */
export interface Visit { id: string; who: DeskKey; to: DeskKey; at: number; say?: string }
/** 일이 끝났을 때 머리 위로 떠오르는 글 ("✓ 결과", 사람 승인은 "👤 승인") */
export interface Pop { id: string; desk: DeskKey; text: string; at: number; human?: boolean }
/** 보드 카드를 사건 진행 대신 다른 글로 (캠페인 리플레이) */
export interface BoardText { title: string; value: string; done: number; total: number }

export interface OfficeView {
  desks: Record<DeskKey, DeskView>;
  /** 방금 들어온 걸어가기 (총괄이 맡긴 부서 → 총괄 앞, 부서끼리 부탁 A → B 옆). 총괄은 걷지 않는다 */
  visits: Visit[];
  pops: Pop[];
  status: OfficeStatus;
  incident: OrchIncident | null;
  progress: { done: number; total: number };
  board?: BoardText;
  /** 화면 전체를 잠깐 어둡게 하고 가운데 띄우는 글 ("7일 뒤"). progress 0~1 */
  overlay?: { text: string; progress: number } | null;
}

/** 말풍선은 처음 본 뒤 이만큼만 보인다 */
export const BUBBLE_MS = 4500;
/** "✓ 결과"가 떠올라 사라지는 시간 */
export const POP_MS = 2400;
const WALK_MS = 3200;
/** 자리가 없는 상대(걷지 않는다). memory는 데이터 자리, safety는 재무 자리에 앉혀 보이지만 걸어갈 사람은 아니다 */
const NO_WALK = new Set(["safety", "memory", "shop-api", "dashboard-api", "operator", "human"]);

/** 숫자 덩어리: 쉼표·소수점과 뒤에 붙은 단위까지 한 덩어리 */
const NUMBER = /\d[\d,.]*(?:%|원|만원|억원|만|억|건|명|개|초|분|시간|일|배|ms|px)?/g;

/**
 * 말풍선 글을 n자 안으로 줄이고 "…"로 끝낸다. 숫자 덩어리 중간에서는 자르지 않는다:
 * 자를 자리가 숫자 안이면 그 숫자 앞 띄어쓰기에서 자른다 (숫자는 통째로 넣거나 통째로 뺀다)
 */
export function short(s: string, n = 14): string {
  if (s.length <= n) return s;
  let cut = n - 1;
  for (const m of s.matchAll(NUMBER)) {
    const start = m.index ?? 0, end = start + m[0].length;
    if (start >= cut) break;
    if (end <= cut) continue;
    const space = s.lastIndexOf(" ", start);
    // 앞에 띄어쓰기가 없으면 숫자 바로 앞에서. 맨 앞부터 숫자라 남는 글이 없으면 숫자를 통째로 넣는다
    cut = space > 0 ? space : start > 0 ? start : end;
    break;
  }
  return `${s.slice(0, cut).trimEnd()}…`;
}

/** 가장 최근 사건: 열린 것이 있으면 그것, 없으면 마지막 것 */
export function focusIncident(incidents: OrchIncident[]): OrchIncident | null {
  const open = [...incidents].reverse().find((i) => i.status === "open");
  return open ?? incidents[incidents.length - 1] ?? null;
}

/**
 * 사무실 상태. `seen`은 말풍선 id → 화면이 처음 본 시각(ms). 처음 연결했을 때 이미 있던 기록은 0으로 넣어 말풍선을 띄우지 않는다.
 */
export function deriveOffice(
  incidents: OrchIncident[], items: ChatItem[], seen: ReadonlyMap<string, number>, now: number,
  opts: { desks?: Partial<Record<DeskKey, Omit<DeskView, "bubble"> & { bubble?: Bubble | null }>>; board?: BoardText; overlay?: OfficeView["overlay"] } = {},
): OfficeView {
  const desks = Object.fromEntries(DESKS.map((d) => [d.key, { state: "idle", bubble: null, skill: null } as DeskView])) as Record<DeskKey, DeskView>;
  const inc = focusIncident(incidents);
  // 사건 없이 상태를 정하는 화면(캠페인 리플레이)은 자리 상태를 바로 준다
  for (const [k, d] of Object.entries(opts.desks ?? {})) if (d) desks[k as DeskKey] = { ...d, bubble: d.bubble ?? null };
  const visits: Visit[] = [];
  const pops: Pop[] = [];

  // 1) 진행 중인 단계 → 작업 중 / 승인 대기
  if (inc && inc.status === "open") {
    for (const step of inc.steps) {
      const k = deskOf(step.dept);
      if (!k) continue;
      if (step.status === "running") desks[k] = { state: "working", bubble: { text: short(skillKo(step.skill)), kind: "think" }, skill: step.skill };
      if (step.status === "held" || inc.pending_holds.some((h) => h.step_id === step.step_id)) desks[k] = { state: "waiting", bubble: null, skill: step.skill };
    }
  }

  // 2) 방금 들어온 말풍선 → 말한 부서 머리 위에 (승인 대기 표시는 덮지 않는다)
  for (const it of items) {
    if (it.kind === "hold" && it.state === "approved") {
      // 보류 카드는 승인돼도 id가 같아서, 승인을 처음 본 시각은 `id:approved`로 따로 기억한다
      const at = seen.get(`${it.id}:approved`) ?? 0;
      const age = now - at;
      if (at > 0 && age >= 0) {
        const step = it.stepId ? inc?.steps.find((s) => s.step_id === it.stepId) : undefined;
        const desk = deskOf(it.dept ?? step?.dept) ?? "orchestrator";
        if (age < POP_MS) pops.push({ id: `${it.id}:approved`, desk, text: "👤 승인", at, human: true });
        if (!it.dept && age < BUBBLE_MS) desks.orchestrator.bubble = { text: "사람 승인 받음", kind: "ok" };
      }
      continue;
    }
    const at = seen.get(it.id) ?? 0;
    const age = now - at;
    if (at === 0 || age < 0) continue;
    if (it.kind === "system" && it.id.endsWith(":open") && age < BUBBLE_MS) {
      desks.orchestrator.bubble = { text: "장애 접수!", kind: "alert" };
    }
    if (it.kind !== "msg") continue;
    const k = it.central ? "orchestrator" : deskOf(it.fromKey);
    // 총괄이 단계를 맡기면 그 부서가 총괄 앞으로 와서 받아 간다 (총괄은 라운지에 있다)
    if (it.central && it.id.endsWith(":req") && age < WALK_MS) {
      const stepId = it.id.split(":").slice(-2, -1)[0];
      const step = inc?.steps.find((s) => s.step_id === stepId);
      const who = deskOf(step?.dept);
      if (who && who !== "orchestrator" && !NO_WALK.has(step?.dept ?? "")) visits.push({ id: it.id, who, to: "orchestrator", at });
    }
    // 부서끼리 직접 부탁(공통 로그 A → B)하면 A가 B 옆으로 걸어가 전한다
    if (!it.central && it.tone === "request" && it.id.startsWith("log:") && age < WALK_MS && it.toKey
      && !NO_WALK.has(it.fromKey) && !NO_WALK.has(it.toKey)) {
      const who = deskOf(it.fromKey), to = deskOf(it.toKey);
      if (who && to && who !== to && who !== "orchestrator") visits.push({ id: it.id, who, to, at, say: short(it.text) });
    }
    if (!k) continue;
    const fact = it.facts[0];
    // 결과 줄(부서 → 총괄 결과, 공통 로그 completed)은 머리 위로 "✓ 결과"도 띄운다
    if (!it.central && it.tone === "ok" && age < POP_MS) {
      pops.push({ id: it.id, desk: k, text: `✓ ${fact ? `${fact.label} ${fact.value}` : short(it.text)}`, at });
    }
    if (age >= BUBBLE_MS || desks[k].state === "waiting") continue;
    const text = it.central ? short(it.text.replace(/ 요청합니다\.$/, " 부탁해요"), 13) : fact ? short(`${fact.label} ${fact.value}`) : short(it.text);
    const kind: BubbleKind = it.tone === "ok" ? "ok" : it.tone === "warn" || it.tone === "fail" ? "warn" : "say";
    desks[k] = { ...desks[k], bubble: { text, kind } };
  }

  const status: OfficeStatus = !inc ? "ok" : inc.status === "open" ? "alert" : inc.status === "degraded" ? "degraded" : "resolved";
  const counted = inc ? inc.steps.filter((s) => s.dept) : [];
  const done = counted.filter((s) => ["completed", "skipped", "failed", "rejected_by_human"].includes(s.status)).length;
  return { desks, visits, pops, status, incident: inc, progress: { done, total: counted.length }, board: opts.board, overlay: opts.overlay ?? null };
}

// ---------------------------------------------------------------- SCN-A 리플레이
// 총괄이 결제 장애(시나리오 A, 롤백 C 등급 보류)를 처리하는 흐름을 총괄 응답 모양(OrchIncident) 그대로 만든다.
// 문장은 AI 대화방과 같은 buildTimeline이 만든다. 숫자는 시연용 예시 값이다.

interface ReplayStep { id: string; skill: string; label: string; dept: string; grade: string | null; start: number; end: number; output: Record<string, unknown>; hold?: { at: number; approve: number } }

const SCN_A: ReplayStep[] = [
  { id: "s1", skill: "diagnose_incident", label: "원인 진단 (infra)", dept: "infra", grade: null, start: 1.4, end: 3.6, output: { severity: "critical", impact_scope: "payment" } },
  { id: "s2", skill: "anomaly_detection", label: "이상 탐지 (data)", dept: "data", grade: null, start: 4.0, end: 6.6, output: { failed_order_count: 1284, affected_customer_count: 412 } },
  { id: "s3", skill: "impact_analysis", label: "영향 분석 (data)", dept: "data", grade: null, start: 7.0, end: 8.8, output: { damage_amount: 38420000, impact_scope: "payment" } },
  { id: "s4", skill: "find_related_prs", label: "관련 코드 변경 찾기 (dev)", dept: "dev", grade: null, start: 9.2, end: 11.0, output: { candidates: [{ number: 231 }] } },
  { id: "s5", skill: "rollback_deployment", label: "배포 되돌리기 (infra)", dept: "infra", grade: "C", start: 11.4, end: 17.6, output: { rolled_back_to: "v2.4.1", status: "rolled_back" }, hold: { at: 12.2, approve: 15.8 } },
  { id: "s6", skill: "verify_recovery", label: "복구 확인 (infra)", dept: "infra", grade: null, start: 18.0, end: 19.6, output: { recovered: true, infra_metrics_ok: true } },
  { id: "s7", skill: "draft_notice", label: "공지 초안 작성 (marketing)", dept: "marketing", grade: "A", start: 20.0, end: 21.4, output: { state: "draft" } },
  { id: "s8", skill: "publish_notice", label: "공지 게시 (marketing)", dept: "marketing", grade: "B", start: 21.8, end: 23.2, output: { channel_code: "banner", state: "published" } },
  { id: "s9", skill: "issue_coupon", label: "보상 쿠폰 지급 (support)", dept: "support", grade: "B", start: 23.6, end: 25.4, output: { issued: 412, amount_per_customer: 3000, total_amount: 1236000 } },
  { id: "s10", skill: "loss_aggregation", label: "손실 리포트 (finance)", dept: "finance", grade: null, start: 25.8, end: 27.6, output: { lost_amount: 38420000, compensation_total: 1236000 } },
  { id: "s11", skill: "check_plan", label: "예전 결정과 비교 (hr)", dept: "hr", grade: null, start: 28.0, end: 29.4, output: { status: "pass" } },
  { id: "s12", skill: "record_case", label: "사건 기록 (memory)", dept: "memory", grade: "A", start: 29.8, end: 31.2, output: { severity: "critical" } },
];
export const REPLAY_END = 33.5;
const REPLAY_CLOSE = 32.0;

/** 리플레이 `t`초 시점의 사건. `base`는 리플레이를 시작한 시각(ms) */
export function replayIncident(t: number, base: number): OrchIncident {
  const iso = (s: number) => new Date(base + s * 1000).toISOString();
  const events: OrchIncident["events"] = [];
  const pending: OrchIncident["pending_holds"] = [];
  const steps: OrchStep[] = SCN_A.map((r) => {
    const s: OrchStep = {
      step_id: r.id, skill: r.skill, label: r.label, dept: r.dept, status: "pending", attempts: 0, note: "", grade: r.grade,
      started_at: null, finished_at: null, transferred_to: null, reason_code: null, output: {},
    };
    if (t < r.start) return s;
    s.started_at = iso(r.start);
    s.attempts = 1;
    s.status = "running";
    if (r.hold && t >= r.hold.at) {
      s.reason_code = "human_input";
      events.push({ at: iso(r.hold.at), message: `${r.label}: C 등급 보류 — 승인 대기열에 올림` });
      if (t < r.hold.approve) {
        s.status = "held";
        pending.push({ step_id: r.id, skill: r.skill, label: r.label });
        return s;
      }
      s.attempts = 2;
      events.push({ at: iso(r.hold.approve), message: `${r.label}: 사람이 승인 — 재개` });
    }
    if (t >= r.end) { s.status = "completed"; s.finished_at = iso(r.end); s.output = r.output; }
    return s;
  });
  const closed = t >= REPLAY_CLOSE;
  return {
    context_id: "replay-scn-a",
    kind: "payment_outage",
    title: "payment 장애 감지 (payment_fail_rate)",
    component: "payment",
    status: closed ? "resolved" : "open",
    opened_at: iso(0.6),
    closed_at: closed ? iso(REPLAY_CLOSE) : null,
    steps: t < 0.6 ? [] : steps,
    events,
    summary: [],
    metrics: closed ? { steps_total: SCN_A.length, steps_completed: SCN_A.length, held: 1, mttr_seconds: Math.round(REPLAY_CLOSE - 0.6) } : {},
    pending_holds: pending,
  };
}

// ---------------------------------------------------------------- 캠페인 리플레이
// 데이터 → 기획 → 사람 확인 → 마케팅 → 재무 → 데이터 → 기획 → 재무로 이어지는 캠페인 흐름을 공통 로그 줄(ChatItem) 모양으로 만든다.
// 화면 안에서만 만드는 예시다(저장소·공통 로그에 넣지 않는다). 숫자는 시연용 값이다.
// skill 이름은 실제 코드의 것을 쓴다: 기획 receive_insight·receive_campaign_update, 마케팅 receive_campaign_brief·start_campaign,
// 재무 review_campaign_budget·record_campaign_result, 데이터 trend_scan·campaign_result. 기획의 "제안 작성"은 해당 skill이 없어 예시 문장으로만 둔다.

export const CAMPAIGN_END = 46;
export const CAMPAIGN_STEPS = 10;
const CAMPAIGN_ID = "replay-campaign";

type CampaignDesk = Omit<DeskView, "bubble"> & { bubble?: Bubble | null };
interface CampaignMsg { at: number; id: string; from: DeskKey; to: DeskKey; skill: string; text: string; tone: "request" | "ok"; fact?: { label: string; value: string } }
interface CampaignWork { from: number; to: number; desk: DeskKey; label: string; state?: "working" | "waiting" }

/** 단계가 시작하는 시각 (보드 카드 "단계 n/10") */
const CAMPAIGN_STEP_AT = [1.0, 5.0, 8.2, 11.4, 15.0, 18.6, 24.0, 28.4, 30.4, 37.4];
const CAMPAIGN_MSGS: CampaignMsg[] = [
  { at: 5.0, id: "c2a", from: "data", to: "planning", skill: "receive_insight", text: "인사이트 전달 부탁해요", tone: "request", fact: { label: "주제", value: "책추천 숏츠" } },
  { at: 7.6, id: "c2b", from: "planning", to: "data", skill: "receive_insight", text: "인사이트 받기 끝났어요", tone: "ok" },
  { at: 15.0, id: "c5a", from: "planning", to: "marketing", skill: "receive_campaign_brief", text: "캠페인 실행 부탁해요", tone: "request", fact: { label: "캠페인", value: "책추천 숏츠" } },
  { at: 17.8, id: "c5b", from: "marketing", to: "planning", skill: "receive_campaign_brief", text: "캠페인 브리프 받기 끝났어요", tone: "ok" },
  { at: 18.6, id: "c6a", from: "marketing", to: "finance", skill: "review_campaign_budget", text: "예산 의견 부탁해요", tone: "request", fact: { label: "요청", value: "30만 원" } },
  { at: 23.0, id: "c6b", from: "finance", to: "marketing", skill: "review_campaign_budget", text: "예산 의견 끝났어요", tone: "ok", fact: { label: "예산", value: "권장 · 30만 원" } },
  { at: 27.4, id: "c7b", from: "marketing", to: "planning", skill: "start_campaign", text: "캠페인 시작 끝났어요", tone: "ok" },
  { at: 33.6, id: "c9a", from: "data", to: "planning", skill: "receive_campaign_update", text: "캠페인 결과 전달 부탁해요", tone: "request", fact: { label: "결과", value: "더 팔림" } },
  { at: 36.4, id: "c9b", from: "planning", to: "data", skill: "receive_campaign_update", text: "캠페인 결과 받기 끝났어요", tone: "ok" },
  { at: 37.4, id: "c10a", from: "marketing", to: "finance", skill: "record_campaign_result", text: "결과 기록 부탁해요", tone: "request" },
  { at: 42.0, id: "c10b", from: "finance", to: "marketing", skill: "record_campaign_result", text: "결과 기록 끝났어요", tone: "ok", fact: { label: "손익", value: "+42만 원 · 줄일 곳 1개" } },
];
const CAMPAIGN_WORK: CampaignWork[] = [
  { from: 1.0, to: 5.0, desk: "data", label: "트렌드 감지" },
  { from: 8.2, to: 11.4, desk: "planning", label: "제안 작성(예시)" },
  { from: 11.4, to: 14.4, desk: "planning", label: "사람 확인 기다림", state: "waiting" },
  { from: 19.6, to: 23.0, desk: "finance", label: "예산 의견" },
  { from: 24.0, to: 27.4, desk: "marketing", label: "캠페인 시작" },
  { from: 30.4, to: 33.6, desk: "data", label: "성과 측정" },
  { from: 38.6, to: 42.0, desk: "finance", label: "결과 기록" },
];
const CAMPAIGN_HOLD = { at: 11.4, approve: 14.4 };
const CAMPAIGN_LATER = { from: 28.4, to: 30.0, text: "7일 뒤" };

/** 캠페인 리플레이 `t`초 시점. `base`는 리플레이를 시작한 시각(ms) */
export function replayCampaign(t: number, base: number): { items: ChatItem[]; desks: Partial<Record<DeskKey, CampaignDesk>>; board: BoardText; overlay: OfficeView["overlay"] } {
  const iso = (s: number) => new Date(base + s * 1000).toISOString();
  const items: ChatItem[] = [];
  const push = (at: number, it: ChatItem) => { if (t >= at) items.push(it); };
  push(0.6, { kind: "separator", id: `${CAMPAIGN_ID}:start`, at: iso(0.6), room: ALL_ROOM, text: "캠페인 리플레이 · 책추천 숏츠 (예시)", variant: "topic" });
  push(1.0, { kind: "system", id: `${CAMPAIGN_ID}:scan`, at: iso(1.0), room: ALL_ROOM, text: "데이터분석 AI가 트렌드를 살펴봐요 (trend_scan, 예시)" });
  push(8.2, { kind: "system", id: `${CAMPAIGN_ID}:draft`, at: iso(8.2), room: ALL_ROOM, text: "기획 AI가 캠페인 제안을 써요 (예시)" });
  if (t >= CAMPAIGN_HOLD.at) {
    items.push({
      kind: "hold", id: `${CAMPAIGN_ID}:hold`, at: iso(CAMPAIGN_HOLD.at), room: ALL_ROOM, title: "캠페인 제안 확인", detail: "기획 AI · 마케팅 실행 전에 사람이 확인해요 (예시)",
      state: t >= CAMPAIGN_HOLD.approve ? "approved" : "pending", contextId: CAMPAIGN_ID, dept: "planning",
    });
  }
  push(24.0, { kind: "system", id: `${CAMPAIGN_ID}:start-campaign`, at: iso(24.0), room: ALL_ROOM, text: "마케팅 AI가 캠페인을 시작해요 (start_campaign, 예시)" });
  push(CAMPAIGN_LATER.from, { kind: "separator", id: `${CAMPAIGN_ID}:later`, at: iso(CAMPAIGN_LATER.from), room: ALL_ROOM, text: "7일 뒤", variant: "topic" });
  push(30.4, { kind: "system", id: `${CAMPAIGN_ID}:measure`, at: iso(30.4), room: ALL_ROOM, text: "데이터분석 AI가 캠페인 성과를 재요 (campaign_result, 예시)" });
  for (const m of CAMPAIGN_MSGS) {
    push(m.at, {
      kind: "msg", id: `log:${CAMPAIGN_ID}:${m.id}`, at: iso(m.at), room: ALL_ROOM, from: speaker(m.from), to: speaker(m.to), fromKey: m.from, toKey: m.to, central: false,
      text: m.text, tag: "캠페인 리플레이 · 예시", raw: `skill: ${m.skill}`, tone: m.tone, facts: m.fact ? [m.fact] : [],
    });
  }
  items.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));

  const desks: Partial<Record<DeskKey, CampaignDesk>> = {};
  for (const w of CAMPAIGN_WORK) {
    if (t < w.from || t >= w.to) continue;
    desks[w.desk] = { state: w.state ?? "working", skill: w.label, bubble: w.state === "waiting" ? null : { text: short(w.label), kind: "think" } };
  }
  const step = CAMPAIGN_STEP_AT.filter((s) => t >= s).length;
  const later = t >= CAMPAIGN_LATER.from && t < CAMPAIGN_LATER.to ? (t - CAMPAIGN_LATER.from) / (CAMPAIGN_LATER.to - CAMPAIGN_LATER.from) : null;
  return {
    items, desks,
    board: { title: "책추천 숏츠 캠페인", value: `단계 ${step}/${CAMPAIGN_STEPS}`, done: step, total: CAMPAIGN_STEPS },
    overlay: later === null ? null : { text: CAMPAIGN_LATER.text, progress: later },
  };
}
