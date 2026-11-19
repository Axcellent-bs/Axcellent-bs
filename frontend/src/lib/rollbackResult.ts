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

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);

/** output.path(승인 경로)를 사람 말로. 모르는 값은 그대로 */
export function pathLabel(v: unknown): string | null {
  const p = str(v);
  if (!p) return null;
  return p === "auto" ? "자동 실행" : p === "human_approved" ? "사람 승인 뒤 실행" : p;
}

const TOOL_NAME: Record<string, string> = { github_actions: "GitHub Actions", jenkins: "Jenkins" };

/** 무엇으로 되돌렸는지: 배포 도구 다시 배포 · 결제 설정 되돌림 · 장애 해제(시연). 근거 키가 없으면 null */
function methodLabel(output: Record<string, unknown>): string | null {
  const r = parseRedeploy(output);
  if (r) return `${TOOL_NAME[r.tool] ?? r.tool}로 다시 배포${r.runId ? ` (실행 ${r.runId})` : ""}`;
  if (isRecord(output.payment_version)) return "결제 설정 되돌림";
  if (output.fault_released === true) return "장애 주입 해제";
  if (output.fault_released === false) return "장애 주입 해제 안 됨";
  return null;
}

/** 되돌리기가 끝난 단계의 결과 한 줄. 없는 값은 빼고 "모름"(unknown)만 버전 자리에 쓴다 */
export function rollbackDoneLine(raw: Record<string, unknown> | null | undefined, unknown = "모름"): string {
  const output = isRecord(raw) ? raw : {};
  const pv = isRecord(output.payment_version) ? output.payment_version : {};
  const before = str(pv.before) ?? str(output.from_version);
  const after = str(pv.after) ?? str(output.rolled_back_to) ?? str(output.to_version);
  const parts = [`되돌림 완료 · 전 ${before ?? unknown} → 후 ${after ?? unknown}`];
  const method = methodLabel(output);
  if (method) parts.push(method);
  const path = pathLabel(output.path);
  if (path) parts.push(path);
  if (output.post_check_ok === true) parts.push("되돌린 뒤 확인 통과");
  else if (output.post_check_ok === false) parts.push("되돌린 뒤 확인 실패");
  return parts.join(" · ");
}
