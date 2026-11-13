// 장애 사건 기록(총괄 GET /orch/api/incidents)에서 화면이 쓰는 사실을 고르는 순수 함수.
// env를 읽지 않아 node --test로 바로 돈다 (incidentFacts.test.ts). 값이 없으면 null(지어내지 않는다).
import type { OrchIncident } from "./agentChat";

/** 장애 종류 이름 (쇼핑몰이 사건 신호에 실은 시험·시연 장애 종류) */
export const FAULT_KIND_KO: Record<string, string> = { switch: "장애 스위치", pg_slow: "결제사 지연", payment_version: "잘못된 배포" };
/** 같은 유형을 가르는 값(event_type) → 이름. 표에 없으면 받은 값 그대로 */
export const EVENT_TYPE_KO: Record<string, string> = { "payment:error_rate_5xx": "결제 오류(5xx)", "payment:p95_latency_ms": "결제 응답 지연" };
export const eventTypeName = (t: string | null | undefined) => (t ? EVENT_TYPE_KO[t] ?? t : "종류 모름");

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const step = (inc: OrchIncident, id: string) => inc.steps.find((s) => s.step_id === id);
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** 장애 종류. 쇼핑몰 신호(fault_kind)가 먼저이고, 없으면(예전 사건) 기록된 조치로 정한다:
 *  결제 설정 배포를 되돌렸으면 잘못된 배포, 장애 상태를 해제했으면 장애 스위치. 둘 다 아니면 모른다(null) */
export function faultKindOf(inc: OrchIncident | null | undefined): string | null {
  if (!inc) return null;
  if (inc.fault_kind && FAULT_KIND_KO[inc.fault_kind]) return FAULT_KIND_KO[inc.fault_kind];
  const rollback = step(inc, "rollback");
  if (rollback?.status === "completed" && isObj(rollback.output?.payment_version)) return FAULT_KIND_KO.payment_version;
  const released = step(inc, "past_fix")?.output?.fault_released === true || rollback?.output?.fault_released === true
    || (isObj(inc.shared?.recovery) && inc.shared.recovery.recovery === "fault_released");
  return released ? FAULT_KIND_KO.switch : null;
}

/** 정상 복귀 = 복구 시간 카드의 끝 시각. 총괄 값(recovery_end_at)이 먼저이고, 없으면(예전 총괄)
 *  감지 시각 + 복구 시간, 그것도 없으면 쇼핑몰 복구 신호 시각 */
export function recoveryEndOf(inc: OrchIncident | null | undefined): string | null {
  if (!inc) return null;
  if (inc.recovery_end_at) return inc.recovery_end_at;
  const start = inc.timeline?.detected_at ? new Date(inc.timeline.detected_at).getTime() : NaN;
  const mttr = finite(inc.metrics?.mttr_seconds);
  if (Number.isFinite(start) && mttr != null) return new Date(start + mttr * 1000).toISOString();
  return inc.timeline?.recovered_at ?? null;
}

/** 복구 검증이 "복구됨"으로 끝난 시각 (인프라 checked_at, 없으면 단계가 끝난 시각) */
export function verifiedAtOf(inc: OrchIncident | null | undefined): string | null {
  const run = inc ? step(inc, "recovery") : undefined;
  if (run?.status !== "completed" || run.output?.recovered !== true) return null;
  const checked = run.output?.checked_at;
  return typeof checked === "string" ? checked : run.finished_at;
}

/** 원인 후보를 시간 순서 규칙으로 골랐는지. 예전 결과는 요약 문구("시간 순서 규칙으로 고름")로 안다 */
export const pickedByRule = (out: { method?: string | null; summary?: string | null } | null | undefined) =>
  out?.method === "rule" || (out?.method == null && /시간 순서 규칙/.test(out?.summary ?? ""));

/** 1차와 마지막 사건의 복구 시간 비교 한 줄 (반올림 정수) */
export function compareLine(first: number, last: number): string {
  if (first <= 0) return "1차 복구 시간이 0초라 비교할 수 없어요";
  const pct = Math.round(((first - last) / first) * 100);
  if (pct === 0) return "1차와 마지막 사건의 복구 시간이 같아요";
  return pct > 0 ? `1차보다 마지막 사건이 ${pct}% 빨라졌어요` : `1차보다 마지막 사건이 ${-pct}% 느려졌어요`;
}

/** 같은 지표 종류(event_type)끼리만 1차와 마지막을 비교한다. `rows`는 감지 순서, 복구 시간이 있는 사건만.
 *  사건이 하나뿐인 종류와 종류를 모르는 사건은 문장을 만들지 않는다(목록에만 나온다) */
export function compareByType(rows: { context_id: string; mttr_seconds?: number | null }[], typeOf: (id: string) => string | null | undefined): string[] {
  const groups = new Map<string, number[]>();
  for (const r of rows) {
    const type = typeOf(r.context_id);
    const secs = finite(r.mttr_seconds);
    if (!type || secs == null) continue;
    groups.set(type, [...(groups.get(type) ?? []), secs]);
  }
  return [...groups.entries()]
    .filter(([, secs]) => secs.length >= 2)
    .map(([type, secs]) => `${eventTypeName(type)}: ${compareLine(secs[0], secs[secs.length - 1])}`);
}
