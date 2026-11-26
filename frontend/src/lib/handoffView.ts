// 트렌드&콘텐츠 "기획 전달" 탭(components/data/PlanningHandoff.tsx)과 인사이트 전달 상태 칩의 순수 계산. node --test (handoffView.test.ts).
// 출처: 기획 AI 상태(GET /state, lib/planning fromBackend)의 proposals 중 from "data"인 것. 단계는 lib/proposals.ts proposalStage를 그대로 쓴다
import type { ReportStatus, TrendReportHandoffOutput, TrendReportListItem } from "./data.ts";
import { PROPOSAL_SKILL_LABEL, handoffFailed, proposalStage, type Proposal } from "./proposals.ts";
import { reportLabel } from "./trendReportView.ts";

/** 보고서 인사이트의 기획 쪽 연결 키 (공통 계약 4번) */
export const REPORT_INSIGHT_PREFIX = "trend_report:";
export const reportInsightId = (reportId: string) => `${REPORT_INSIGHT_PREFIX}${reportId}`;
export const reportIdOfInsight = (insightId: string | null | undefined) =>
  insightId?.startsWith(REPORT_INSIGHT_PREFIX) ? insightId.slice(REPORT_INSIGHT_PREFIX.length) : null;

/** 데이터에서 온 제안만, 최근 받은 순 (created "MM-DD HH:mm"은 같은 해 안에서 글자 순서 = 시간 순서) */
export function dataProposals(all: Proposal[] | null | undefined): Proposal[] {
  return (all ?? []).filter((p) => p.from === "data").sort((a, b) => (b.created ?? "").localeCompare(a.created ?? "") || b.id.localeCompare(a.id));
}

/** 진행 중: 폐기·성과 확인이 아닌 것 (탭 옆 배지 숫자도 이것) */
export const handoffActive = (p: Proposal) => p.status !== "폐기" && p.status !== "성과 확인";
export const activeCount = (list: Proposal[]) => list.filter(handoffActive).length;

export interface HandoffSummary { received: number; active: number; marketing: number; measured: number }
export function handoffSummary(list: Proposal[]): HandoffSummary {
  return {
    received: list.length,
    active: activeCount(list),
    marketing: list.filter((p) => p.handoff?.ok === true).length,
    measured: list.filter((p) => p.status === "성과 확인").length,
  };
}

// ---- 11/26 무리 나누기: 진행 중 → 오래 기다림 → 끝남 → 폐기 (자동으로 지우거나 상태를 바꾸지 않는다)

/** 확인 대기로 이 날 수를 넘으면 "오래 기다림" */
export const STALE_DAYS = 3;
const DAY_MS = 86_400_000;
const KST_MS = 9 * 3_600_000;

/** 기획 기록 created(KST "MM-DD HH:mm", 해 없음) → 시각(ms). 지금보다 하루 넘게 뒤면 지난해로 본다. 모르면 null */
export function createdAt(created: string | null | undefined, now: number): number | null {
  const m = /^(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec((created ?? "").trim());
  if (!m) return null;
  const year = new Date(now + KST_MS).getUTCFullYear();
  const at = (y: number) => Date.UTC(y, Number(m[1]) - 1, Number(m[2]), Number(m[3]), Number(m[4])) - KST_MS;
  const t = at(year);
  return t > now + DAY_MS ? at(year - 1) : t;
}

/** 끝남: 기획이 확정해 넘긴 것(마케팅 전달·진행 중·성과 확인·기획서로). 마케팅 전달 실패는 아직 진행 중 */
const FINISHED: ReadonlySet<string> = new Set(["마케팅 전달", "진행 중", "성과 확인", "기획서로"]);
export type HandoffGroup = "active" | "stale" | "done" | "rejected";
export function handoffGroup(p: Proposal, now: number): HandoffGroup {
  if (p.status === "폐기") return "rejected";
  if (FINISHED.has(p.status) && !handoffFailed(p)) return "done";
  if (p.status === "확인 대기") {
    const at = createdAt(p.created, now);
    if (at !== null && now - at > STALE_DAYS * DAY_MS) return "stale";
  }
  return "active";
}

export type HandoffGroups = Record<HandoffGroup, Proposal[]>;
/** 무리마다 목록 (받은 순서 = dataProposals의 최신 위 그대로) */
export function handoffGroups(list: Proposal[], now: number): HandoffGroups {
  const out: HandoffGroups = { active: [], stale: [], done: [], rejected: [] };
  for (const p of list) out[handoffGroup(p, now)].push(p);
  return out;
}

/** 위 숫자 3칸: 진행 중 · 오래 기다림 · 마케팅 전달(handoff 있음). 지금 기록에서 센 그대로 */
export function handoffCounts(list: Proposal[], now: number): { active: number; stale: number; marketing: number } {
  const g = handoffGroups(list, now);
  return { active: g.active.length, stale: g.stale.length, marketing: list.filter((p) => p.handoff?.ok === true).length };
}

/** 탭 옆 배지: "진행 N" (진행 중 수, 0이면 null) */
export function handoffBadge(list: Proposal[], now: number): string | null {
  const n = handoffGroups(list, now).active.length;
  return n > 0 ? `진행 ${n}` : null;
}

/** 줄 머리 출처 칩: 보고서 인사이트면 "분석 의뢰 · TR-xxxx", 아니면 skill 이름 */
export function sourceChip(p: Pick<Proposal, "skill" | "insightId">): string {
  const rid = reportIdOfInsight(p.insightId);
  if (rid) return `분석 의뢰 · ${reportLabel(rid)}`;
  return PROPOSAL_SKILL_LABEL[p.skill] ?? p.skill;
}

export type CellState = "done" | "current" | "future";
export interface ChainCell { key: "sent" | "proposal" | "plan" | "marketing" | "result"; label: string; state: CellState }
export interface Chain { cells: ChainCell[]; rejected: boolean; note: string | null; warn: string | null }

/**
 * 사슬: 데이터 보냄(created) → 기획 PROP-n → 기획안/확정 → 마케팅(handoff.campaign) → 성과(result.direction).
 * 지금 칸 = 1 + proposalStage (확인 대기·폐기 = PROP 칸, 기획안 = 기획안 칸, 마케팅·진행 중 = 마케팅 칸, 성과 확인 = 성과 칸).
 * 폐기면 PROP 칸에서 멈추고 rejected + note
 */
export function chainOf(p: Proposal): Chain {
  const rejected = p.status === "폐기";
  const current = rejected ? 1 : 1 + proposalStage(p);
  const state = (i: number): CellState => (i < current ? "done" : i === current ? "current" : "future");
  const toDev = p.status === "기획서로";
  const marketing = toDev
    ? `개발 요청${p.request ? ` ${p.request}` : ""}`
    : handoffFailed(p) ? "마케팅 전달 실패" : `마케팅${p.handoff?.campaign ? ` ${p.handoff.campaign}` : ""}`;
  const cells: ChainCell[] = [
    { key: "sent", label: `데이터 보냄${p.created ? ` ${p.created}` : ""}`, state: "done" },
    { key: "proposal", label: `기획 ${p.id}${current === 1 && !rejected ? " · 확인 대기" : ""}`, state: state(1) },
    { key: "plan", label: current > 2 ? "확정" : "기획안", state: state(2) },
    { key: "marketing", label: marketing, state: state(3) },
    { key: "result", label: p.result ? `성과 · ${p.result.direction}` : "성과", state: state(4) },
  ];
  if (rejected) cells.splice(2); // 폐기면 뒤 칸은 그리지 않는다
  return { cells, rejected, note: rejected ? p.note ?? null : null, warn: handoffFailed(p) ? p.handoff?.reason ?? "마케팅에 넘기지 못함" : null };
}

/** 기획 기록 중 이 인사이트를 받은 제안 (from data + insightId 같음) */
export function proposalFor(all: Proposal[] | null | undefined, insightId: string | null | undefined): Proposal | null {
  if (!insightId) return null;
  return (all ?? []).find((p) => p.from === "data" && p.insightId === insightId) ?? null;
}

// ---- 보고서 인사이트 카드 (components/data/ReportInsights.tsx)

/** 인사이트 카드로 보여 줄 보고서: 완료·부분 완료이고 인사이트가 없다고 하지 않은 것 */
export function insightReports(items: TrendReportListItem[]): TrendReportListItem[] {
  const ok: ReportStatus[] = ["done", "partial"];
  return items.filter((i) => ok.includes(i.status) && i.has_insight !== false);
}

export type ChipTone = "ok" | "warn" | "muted" | "bad";
export interface DeliveryChip { text: string; tone: ChipTone; sent: boolean }

/** 기획에 보낸 결과 칩 */
export function handoffChip(h: Pick<TrendReportHandoffOutput, "outcome" | "proposal_id" | "reason">): DeliveryChip {
  switch (h.outcome) {
    case "created":
    case "duplicate":
      return { text: `기획 전달됨${h.proposal_id ? ` · ${h.proposal_id}` : ""}`, tone: "ok", sent: true };
    case "rejected":
      return { text: "기획이 최근 지운 제안과 같아요", tone: "warn", sent: false };
    default:
      return { text: h.reason || "보내지 못했어요", tone: "muted", sent: false };
  }
}

/**
 * 카드의 전달 상태: 기획 기록에 있으면 그것(가장 믿을 만함), 없으면 이번에 보낸 결과·목록의 handoff, 그것도 없으면 "아직 기획에 안 감".
 * sent면 "기획에 보내기" 버튼 대신 칩만
 */
export function deliveryOf(proposal: Pick<Proposal, "id" | "status"> | null, handoff: Pick<TrendReportHandoffOutput, "outcome" | "proposal_id" | "reason"> | null | undefined): DeliveryChip {
  if (proposal) return { text: `기획 전달됨 · ${proposal.id}${proposal.status === "폐기" ? " (폐기)" : ""}`, tone: proposal.status === "폐기" ? "bad" : "ok", sent: true };
  if (handoff) return handoffChip(handoff);
  return { text: "아직 기획에 안 감", tone: "warn", sent: false };
}

/** "기획에 보내기"가 게이트웨이에 막혔는지 (askData의 ApiError status 403·404, 또는 거부 outcome·skill 거절) → "준비 중" */
export function handoffGated(e: { status?: number; message?: string } | null | undefined): boolean {
  if (!e) return false;
  if (e.status === 403 || e.status === 404) return true;
  return /FORBIDDEN_SKILL|skill_not_allowed|\(rejected\)|\(403\)|\(404\)/.test(e.message ?? "");
}
export const HANDOFF_GATED_TEXT = "기획에 보내기 준비 중 (허용 목록 추가 대기)";

// ---- 인사이트 탭 필터 (#recommend?from=report|brief)

export type InsightFilter = "all" | "report" | "brief";
export function insightFilterFromHash(hash: string): InsightFilter {
  const m = /^#recommend\?(.*)$/.exec(hash);
  const v = m ? new URLSearchParams(m[1]).get("from") : null;
  return v === "report" || v === "brief" ? v : "all";
}
export const insightFilterHash = (f: InsightFilter) => (f === "all" ? "#recommend" : `#recommend?from=${f}`);
