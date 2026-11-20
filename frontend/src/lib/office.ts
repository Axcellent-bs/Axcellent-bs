import { deptOf, skillKo, type ChatItem, type OrchIncident } from "./agentChat";
import { applyRequestDesks, requestDesks } from "./officeRequests";
import type { WorkRequestRow } from "./workRequests";

// AI 오피스: AI 대화방과 같은 기록(총괄 사건·안전 관문·공통 로그)을 픽셀 사무실 상태로 바꾼다.
// 상태는 기록된 값(단계 status·말풍선 문장·facts)으로만 정하고, 화면 없는 순수 함수만 둔다. 실시간 기록만 쓴다.

// ---------------------------------------------------------------- 자리
export type DeskKey = "infra" | "data" | "support" | "finance" | "planning" | "marketing" | "hr" | "archive" | "dev" | "orchestrator" | "safety";

export interface DeskDef { key: DeskKey; label: string; x: number; y: number; shirt: string; hair: string; skin: string }

/** 사무실 좌표(논리 픽셀 256×176). 색은 캔버스 픽셀 그림 전용이다 (화면 UI 색은 디자인 토큰을 쓴다).
 *  shirt는 이름표 앞 색 점이고, AI 대화방 대화 기록의 아바타 색(Messenger.tsx AVATAR)과 같게 둔다 */
export const DESKS: DeskDef[] = [
  { key: "infra", label: "인프라", x: 14, y: 62, shirt: "#4a4a6e", hair: "#1b1b1b", skin: "#f2c9a0" },
  { key: "data", label: "데이터분석", x: 74, y: 62, shirt: "#2f7d4f", hair: "#2b1d14", skin: "#f5d0b0" },
  { key: "support", label: "고객지원", x: 134, y: 62, shirt: "#f9a8d4", hair: "#6b3e26", skin: "#f2c9a0" },
  { key: "finance", label: "재무", x: 194, y: 62, shirt: "#a87a0a", hair: "#3a2a1a", skin: "#e8b98f" },
  { key: "planning", label: "기획", x: 14, y: 128, shirt: "#5b5bd6", hair: "#191919", skin: "#f5d0b0" },
  { key: "marketing", label: "마케팅", x: 74, y: 128, shirt: "#bef264", hair: "#7a4a2a", skin: "#f2c9a0" },
  { key: "hr", label: "인사", x: 134, y: 128, shirt: "#f97316", hair: "#4a2f1b", skin: "#e8b98f" },
  // 아카이브: 위 책상 앞쪽 의자(뒷모습). 장애·업무 기록을 남기고 찾아 준다. x·y는 쓰는 곳이 없다
  { key: "archive", label: "아카이브", x: 14, y: 92, shirt: "#14b8a6", hair: "#2b1d14", skin: "#f2c9a0" },
  // 개발: 기획 맞은편 의자(뒷모습). x·y는 쓰는 곳이 없어(그림 자리는 PixelOffice SEATS) 기획 아래쪽 값만 둔다
  { key: "dev", label: "개발", x: 14, y: 150, shirt: "#232345", hair: "#3a2a1a", skin: "#f2c9a0" },
  { key: "orchestrator", label: "총괄 AI", x: 194, y: 128, shirt: "#4c1d95", hair: "#222222", skin: "#f5d0b0" },
  // 안전 처리 로그: 위 책상 앞쪽 의자(뒷모습). 모든 AI의 실행 요청(execute)을 받아 등급을 매긴다. x·y는 쓰는 곳이 없다
  { key: "safety", label: "안전 처리 로그", x: 74, y: 92, shirt: "#fde047", hair: "#1b1b1b", skin: "#f5d0b0" },
];

/** 기록의 부서 이름 → 자리. 회사 기억(memory)은 데이터 부서 담당이라 데이터 자리에 앉힌다. 안전 처리 로그(safety)는 자기 자리가 있다 */
export function deskOf(name: string | null | undefined): DeskKey | null {
  if (!name) return null;
  // 서비스 이름(inquiry·coupon·org 등)은 먼저 부서로 (AI 대화방 이름과 같은 규칙)
  let dept = deptOf(name);
  if (dept === "memory") return "data";
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

export interface OfficeView {
  desks: Record<DeskKey, DeskView>;
  /** 방금 들어온 걸어가기 (총괄이 맡긴 부서 → 총괄 앞, 부서끼리 부탁 A → B 옆). 총괄은 걷지 않는다 */
  visits: Visit[];
  pops: Pop[];
  status: OfficeStatus;
  incident: OrchIncident | null;
  /** 라운지 화이트보드 "사건 진행" 카드 (PixelOffice boardCard) */
  progress: { done: number; total: number };
}

/** 말풍선은 처음 본 뒤 이만큼만 보인다 */
export const BUBBLE_MS = 4500;
/** "✓ 결과"가 떠올라 사라지는 시간 */
export const POP_MS = 2400;
const WALK_MS = 3200;
/** 공통 로그 부탁에서 걷지 않는 상대. memory는 데이터 자리에 앉혀 보이지만 걸어갈 사람은 아니다.
 *  안전 처리 로그(safety)는 공통 로그에도 실행 기록과 같은 일이 남아서, 걷기는 실행 기록(`exe:…:req`)으로만 한 번 한다 */
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
  opts: {
    /** 고객 의뢰(GET /api/requests). 사람 토큰이 없으면 넘기지 않는다(지금 화면 그대로) */
    requests?: WorkRequestRow[] | null;
  } = {},
): OfficeView {
  const desks = Object.fromEntries(DESKS.map((d) => [d.key, { state: "idle", bubble: null, skill: null } as DeskView])) as Record<DeskKey, DeskView>;
  const inc = focusIncident(incidents);
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
  // 1-2) 고객 의뢰(wr_) 단계 → 작업 중 / 승인 대기 (가장 최근 열린 의뢰 하나, 사건이 쓰는 자리는 사건이 먼저. lib/officeRequests.ts)
  applyRequestDesks(desks, requestDesks(opts.requests, now).map((d) => ({ ...d, bubble: d.bubble ? short(d.bubble, 16) : null })), deskOf);

  // 2) 방금 들어온 말풍선 → 말한 부서 머리 위에 (승인 대기 표시는 덮지 않는다)
  for (const it of items) {
    // 안전 처리 로그가 보류해 사람 결정을 기다리는 일이 있으면 안전 처리 로그 자리는 "승인 대기"
    if (it.kind === "hold" && it.state === "pending" && it.id.startsWith("exe:")) desks.safety = { ...desks.safety, state: "waiting" };
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
    // 안전 처리 로그의 판단 줄은 대화방에서 가운데 줄(central)이지만 말한 쪽은 안전 처리 로그 자리다
    const k = it.fromKey === "safety" ? "safety" : it.central ? "orchestrator" : deskOf(it.fromKey);
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
    // 실행 요청(안전 처리 로그 기록): 요청한 AI가 안전 처리 로그 자리 옆으로 가서 맡긴다
    if (it.id.startsWith("exe:") && it.id.endsWith(":req") && age < WALK_MS) {
      const who = deskOf(it.fromKey);
      if (who && who !== "safety" && who !== "orchestrator") visits.push({ id: it.id, who, to: "safety", at, say: short(it.text) });
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
  return { desks, visits, pops, status, incident: inc, progress: { done, total: counted.length } };
}
