import { skillKo, type ChatItem, type OrchIncident, type OrchStep } from "./agentChat";

// AI 오피스: AI 대화방과 같은 기록(총괄 사건·안전 관문·공통 로그)을 픽셀 사무실 상태로 바꾼다.
// 상태는 기록된 값(단계 status·말풍선 문장·facts)으로만 정하고, 화면 없는 순수 함수만 둔다.

// ---------------------------------------------------------------- 자리
export type DeskKey = "infra" | "data" | "support" | "finance" | "planning" | "marketing" | "hr" | "orchestrator";

export interface DeskDef { key: DeskKey; label: string; x: number; y: number; shirt: string; hair: string; skin: string }

/** 사무실 좌표(논리 픽셀 256×176). 색은 캔버스 픽셀 그림 전용이다 (화면 UI 색은 디자인 토큰을 쓴다) */
export const DESKS: DeskDef[] = [
  { key: "infra", label: "인프라", x: 14, y: 62, shirt: "#2a9d8f", hair: "#1b1b1b", skin: "#f2c9a0" },
  { key: "data", label: "데이터분석", x: 74, y: 62, shirt: "#6d28d9", hair: "#2b1d14", skin: "#f5d0b0" },
  { key: "support", label: "고객지원", x: 134, y: 62, shirt: "#047857", hair: "#6b3e26", skin: "#f2c9a0" },
  { key: "finance", label: "재무", x: 194, y: 62, shirt: "#b45309", hair: "#3a2a1a", skin: "#e8b98f" },
  { key: "planning", label: "기획개발", x: 14, y: 128, shirt: "#5b4fd0", hair: "#191919", skin: "#f5d0b0" },
  { key: "marketing", label: "마케팅", x: 74, y: 128, shirt: "#ec7452", hair: "#7a4a2a", skin: "#f2c9a0" },
  { key: "hr", label: "인사", x: 134, y: 128, shirt: "#b91c1c", hair: "#4a2f1b", skin: "#e8b98f" },
  { key: "orchestrator", label: "총괄", x: 194, y: 128, shirt: "#2e2688", hair: "#222222", skin: "#f5d0b0" },
];

/** 기록의 부서 이름 → 자리. 회사 기억(memory)은 데이터 부서 담당이라 데이터 자리에 앉힌다 */
export function deskOf(dept: string | null | undefined): DeskKey | null {
  if (!dept) return null;
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

export interface OfficeView {
  desks: Record<DeskKey, DeskView>;
  /** 총괄이 방금 일을 맡긴 부서. 총괄 캐릭터가 그 자리로 걸어간다 (없으면 자기 자리) */
  walkTo: DeskKey | null;
  status: OfficeStatus;
  incident: OrchIncident | null;
  progress: { done: number; total: number };
}

/** 말풍선은 처음 본 뒤 이만큼만 보인다 */
export const BUBBLE_MS = 4500;
const WALK_MS = 3200;

const short = (s: string, n = 14) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** 가장 최근 사건: 열린 것이 있으면 그것, 없으면 마지막 것 */
export function focusIncident(incidents: OrchIncident[]): OrchIncident | null {
  const open = [...incidents].reverse().find((i) => i.status === "open");
  return open ?? incidents[incidents.length - 1] ?? null;
}

/**
 * 사무실 상태. `seen`은 말풍선 id → 화면이 처음 본 시각(ms). 처음 연결했을 때 이미 있던 기록은 0으로 넣어 말풍선을 띄우지 않는다.
 */
export function deriveOffice(incidents: OrchIncident[], items: ChatItem[], seen: ReadonlyMap<string, number>, now: number): OfficeView {
  const desks = Object.fromEntries(DESKS.map((d) => [d.key, { state: "idle", bubble: null, skill: null } as DeskView])) as Record<DeskKey, DeskView>;
  const inc = focusIncident(incidents);

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
  let walkTo: DeskKey | null = null;
  let walkAt = 0;
  for (const it of items) {
    const at = seen.get(it.id) ?? 0;
    const age = now - at;
    if (at === 0 || age < 0) continue;
    if (it.kind === "system" && it.id.endsWith(":open") && age < BUBBLE_MS) {
      desks.orchestrator.bubble = { text: "장애 접수!", kind: "alert" };
    }
    if (it.kind === "hold" && it.state === "approved" && age < BUBBLE_MS) {
      desks.orchestrator.bubble = { text: "사람 승인 받음", kind: "ok" };
    }
    if (it.kind !== "msg") continue;
    const k = it.central ? "orchestrator" : deskOf(it.fromKey);
    if (it.central && it.id.endsWith(":req") && age < WALK_MS && at > walkAt) {
      const stepId = it.id.split(":").slice(-2, -1)[0];
      const step = inc?.steps.find((s) => s.step_id === stepId);
      const target = deskOf(step?.dept);
      if (target && target !== "orchestrator") { walkTo = target; walkAt = at; }
    }
    if (!k || age >= BUBBLE_MS || desks[k].state === "waiting") continue;
    const fact = it.facts[0];
    const text = it.central ? short(it.text.replace(/ 요청합니다\.$/, " 부탁해요"), 13) : fact ? short(`${fact.label} ${fact.value}`) : short(it.text);
    const kind: BubbleKind = it.tone === "ok" ? "ok" : it.tone === "warn" || it.tone === "fail" ? "warn" : "say";
    desks[k] = { ...desks[k], bubble: { text, kind } };
  }

  const status: OfficeStatus = !inc ? "ok" : inc.status === "open" ? "alert" : inc.status === "degraded" ? "degraded" : "resolved";
  const counted = inc ? inc.steps.filter((s) => s.dept) : [];
  const done = counted.filter((s) => ["completed", "skipped", "failed", "rejected_by_human"].includes(s.status)).length;
  return { desks, walkTo, status, incident: inc, progress: { done, total: counted.length } };
}

// ---------------------------------------------------------------- SCN-A 리플레이
// 총괄이 결제 장애(시나리오 A, 롤백 C 등급 보류)를 처리하는 흐름을 총괄 응답 모양(OrchIncident) 그대로 만든다.
// 문장은 AI 대화방과 같은 buildTimeline이 만든다. 숫자는 시연용 예시 값이다.

interface ReplayStep { id: string; skill: string; label: string; dept: string; grade: string | null; start: number; end: number; output: Record<string, unknown>; hold?: { at: number; approve: number } }

const SCN_A: ReplayStep[] = [
  { id: "s1", skill: "diagnose_incident", label: "원인 진단 (infra)", dept: "infra", grade: null, start: 1.4, end: 3.6, output: { severity: "critical", impact_scope: "payment" } },
  { id: "s2", skill: "anomaly_detection", label: "이상 탐지 (data)", dept: "data", grade: null, start: 4.0, end: 6.6, output: { failed_order_count: 1284, affected_customer_count: 412 } },
  { id: "s3", skill: "impact_analysis", label: "영향 분석 (data)", dept: "data", grade: null, start: 7.0, end: 8.8, output: { damage_amount: 38420000, impact_scope: "payment" } },
  { id: "s4", skill: "find_related_prs", label: "관련 코드 변경 찾기 (planning)", dept: "planning", grade: null, start: 9.2, end: 11.0, output: { candidates: [{ number: 231 }] } },
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
