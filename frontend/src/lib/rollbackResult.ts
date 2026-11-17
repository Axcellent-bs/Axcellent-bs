// 배포 되돌리기(rollback_deployment) 단계의 결과를 화면에 쓸 모양으로 읽는다. 화면 없는 순수 함수만 둔다.
// 인프라 AI가 배포 도구로 다시 배포했으면 output.redeploy = {tool, run_id, url},
// 다시 배포에 실패했으면 note "failed: redeploy_failed" + output.handoff_steps(사람이 할 처리 순서)다.

export const ROLLBACK_SKILL = "rollback_deployment";
/** 총괄이 남기는 실패 메모 (engine._fail: "<상태>: <outcome>") */
export const REDEPLOY_FAILED_NOTE = "failed: redeploy_failed";

export interface Redeploy { tool: string; runId: string | null; url: string | null }
export type RollbackView =
  | { kind: "redeployed"; redeploy: Redeploy }
  | { kind: "handoff"; steps: string[] };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** http·https 주소만 링크로 쓴다 (javascript: 등은 버린다) */
export function safeHttpUrl(v: unknown): string | null {
  if (typeof v !== "string" || !v) return null;
  try {
    const u = new URL(v);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** output.redeploy를 읽는다. 도구 이름이 없으면 null */
export function parseRedeploy(output: Record<string, unknown>): Redeploy | null {
  const r = output.redeploy;
  if (!isRecord(r) || typeof r.tool !== "string" || !r.tool) return null;
  const runId = typeof r.run_id === "string" || typeof r.run_id === "number" ? String(r.run_id) : null;
  return { tool: r.tool, runId, url: safeHttpUrl(r.url) };
}

/** output.handoff_steps를 읽는다. 문자열만 남긴다 */
export function parseHandoffSteps(output: Record<string, unknown>): string[] {
  const s = output.handoff_steps;
  return Array.isArray(s) ? s.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
}

export const isRedeployFailed = (note: string | null | undefined): boolean => note === REDEPLOY_FAILED_NOTE;

/** 배포 되돌리기 단계에서 화면에 더 보여 줄 것. 되돌리기가 아니거나 보여 줄 게 없으면 null */
export function rollbackView(step: { skill: string; note: string; output: Record<string, unknown> | null | undefined }): RollbackView | null {
  if (step.skill !== ROLLBACK_SKILL) return null;
  const output = isRecord(step.output) ? step.output : {};
  if (isRedeployFailed(step.note)) return { kind: "handoff", steps: parseHandoffSteps(output) };
  const redeploy = parseRedeploy(output);
  return redeploy ? { kind: "redeployed", redeploy } : null;
}
