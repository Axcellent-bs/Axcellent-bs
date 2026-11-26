// 대화방 의뢰 카드의 "사건 롤백 상태" (11/26 롤백 요청 2단계, 읽기 전용). 화면 없는 순수 함수만 둔다.
// 총괄 사건 목록(GET /orch/api/incidents, 로그인 회사 범위)에서 열린 사건의 롤백 단계를 읽어 보여 주기만 한다.
// 승인·거절·실행 호출은 여기에 없다(롤백은 기존 사건 흐름의 안전 관문에서만 한다). 값이 없으면 지어내지 않고 null.
import type { OrchIncident, OrchStep } from "./agentChat.ts";

export const ROLLBACK_STEP_ID = "rollback";
/** 거절하면 일어나는 일 (엔진: 사람이 거절하면 이관 큐로 넘어가 사람이 장애를 직접 푼다) */
export const REJECT_CONSEQUENCE = "거절하면 이관 큐로 넘어가 사람이 직접 처리해요";
export const NO_OPEN_INCIDENT = "열린 사건이 없어요. 장애가 감지되면 사건에서 롤백을 제안해요";

export type RollbackState = "no_proposal" | "checking" | "waiting" | "done" | "rejected" | "handed" | "skipped" | "failed";
export type Confidence = "low" | "mid" | "high";

export interface RollbackStatusView {
  contextId: string;
  /** 승인 대기 보류의 안전 관문 review_id (대기 중이고 총괄이 값을 줄 때만) */
  reviewId: string | null;
  title: string;
  openedAt: string | null;
  state: RollbackState;
  /** 상태 한 줄 (모든 상태에 있다) */
  headline: string;
  service: string | null;
  fromVersion: string | null;
  toVersion: string | null;
  /** 되돌릴 버전의 직전 health. 배포 기록에 값이 없으면 null("확인 안 됨"). 나중에 채울 수 있게 선택 필드로만 읽는다 */
  toVersionHealthy: boolean | null;
  confidence: Confidence | null;
  irreversibleCount: number | null;
  priorRollbacks: number | null;
  /** 승인 대기 마감 (대기 중일 때만, 총괄 pending_holds) */
  expiresAt: string | null;
  /** 대기 중일 때만: 거절하면 일어나는 일 */
  onReject: string | null;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);
const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);

/** 롤백 단계가 있고 진행 중이거나 장애가 안 풀린 채 대응이 끝난 사건 (백엔드 rollback_status.is_open과 같은 기준), 최근에 열린 것부터 */
export function openRollbackIncidents(incidents: readonly OrchIncident[]): OrchIncident[] {
  return incidents
    .filter((i) => i.steps.some((s) => s.step_id === ROLLBACK_STEP_ID) && (i.status === "open" || (i.status === "degraded" && !i.recovered_at)))
    .sort((a, b) => String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")));
}

const HEADLINE: Record<RollbackState, string> = {
  no_proposal: "롤백 제안 없음",
  checking: "롤백을 검토하는 중이에요",
  waiting: "롤백 승인 대기 중",
  done: "롤백을 실행했어요",
  rejected: "사람이 롤백을 거절했어요. 이관 큐로 넘어갔어요",
  handed: "롤백하지 않고 이관 큐로 넘겼어요",
  skipped: "롤백하지 않았어요",
  failed: "롤백을 끝내지 못했어요",
};

function stateOf(step: OrchStep | undefined): RollbackState {
  switch (step?.status) {
    case "held": return "waiting";
    case "completed": return "done";
    case "rejected_by_human": return "rejected";
    case "transfer_queued": return "handed";
    case "skipped": return "skipped";
    case "failed": return "failed";
    case "running": return "checking";
    default: return "no_proposal";
  }
}

/** 원인 후보(진단 단계 결과)에서 되돌릴 배포의 확신도. 같은 서비스·버전이 없으면 첫 후보 */
function confidenceOf(inc: OrchIncident, service: string | null, version: string | null): { confidence: Confidence | null; service: string | null; version: string | null } {
  const diag = inc.steps.find((s) => s.step_id === "diagnose");
  const list = isRecord(diag?.output) && Array.isArray(diag.output.suspects) ? diag.output.suspects.filter(isRecord) : [];
  const hit = list.find((s) => s.service === service && s.version === version) ?? list[0];
  const c = hit?.confidence;
  return { confidence: c === "low" || c === "mid" || c === "high" ? c : null, service: str(hit?.service), version: str(hit?.version) };
}

/** 한 사건의 롤백 상태. 단계 결과에 없는 값은 null */
export function rollbackStatusOf(inc: OrchIncident): RollbackStatusView {
  const step = inc.steps.find((s) => s.step_id === ROLLBACK_STEP_ID);
  const state = stateOf(step);
  const out = isRecord(step?.output) ? step.output : {};
  const params = isRecord(out.path_params) ? out.path_params : {};
  const own = { service: str(out.service), from: str(out.from_version) };
  const diag = confidenceOf(inc, own.service, own.from);
  const hold = inc.pending_holds.find((h) => h.step_id === ROLLBACK_STEP_ID);
  // 아직 제안 전이면 값은 보이지 않는다(원인 후보만으로 롤백 제안이라고 말하지 않는다)
  const shown = state !== "no_proposal" && state !== "checking";
  return {
    contextId: inc.context_id,
    reviewId: state === "waiting" ? str(hold?.review_id) : null,
    title: inc.title,
    openedAt: inc.opened_at,
    state,
    headline: HEADLINE[state],
    service: shown ? own.service ?? diag.service : null,
    fromVersion: shown ? own.from ?? diag.version : null,
    toVersion: shown ? str(out.to_version) : null,
    toVersionHealthy: shown && typeof out.to_version_healthy === "boolean" ? out.to_version_healthy : null,
    confidence: shown ? diag.confidence : null,
    irreversibleCount: shown ? int(params.irreversible_count) : null,
    priorRollbacks: shown ? int(params.prior_rollbacks) : null,
    expiresAt: state === "waiting" ? str(hold?.expires_at) : null,
    onReject: state === "waiting" ? REJECT_CONSEQUENCE : null,
  };
}

/** 카드에 보일 결과: 열린 사건이 없으면 안내, 있으면 가장 최근 사건의 상태(와 열린 사건 수) */
export type RollbackCardResult =
  | { kind: "none"; text: string }
  | { kind: "status"; status: RollbackStatusView; openCount: number };

export function rollbackCardResult(incidents: readonly OrchIncident[]): RollbackCardResult {
  const open = openRollbackIncidents(incidents);
  if (open.length === 0) return { kind: "none", text: NO_OPEN_INCIDENT };
  return { kind: "status", status: rollbackStatusOf(open[0]), openCount: open.length };
}

// ---- 카드 안 결정 영역 (3-a). 결정은 기존 안전 관문 결정 경로(ChatReviewDecision 컴포넌트) 한 곳뿐이다 ----

/** 서비스 이름 확인: 앞뒤 공백만 지우고 대소문자는 구분한다. 빈 값·서비스 없음은 불가 */
export function serviceNameMatches(typed: string, service: string | null | undefined): boolean {
  const want = (service ?? "").trim();
  return want !== "" && typed.trim() === want;
}

export interface RollbackDecisionView {
  reviewId: string;
  service: string;
  /** 승인 확인 문구. 카드 데이터에 없는 값은 지어내지 않고 뺀다 */
  confirmText: string;
}

/** 롤백이 승인 대기(held)이고 review_id와 서비스 이름이 있을 때만 결정 영역을 보인다. 그 밖에는 null(읽기 전용) */
export function rollbackDecisionOf(s: RollbackStatusView): RollbackDecisionView | null {
  if (s.state !== "waiting" || !s.reviewId || !s.service) return null;
  const ver = s.fromVersion && s.toVersion ? ` ${s.fromVersion}→${s.toVersion}` : s.toVersion ? ` (되돌릴 버전 ${s.toVersion})` : "";
  return { reviewId: s.reviewId, service: s.service, confirmText: `운영에서 ${s.service}${ver} 롤백이 실행돼요. 되돌릴 수 없어요.` };
}
