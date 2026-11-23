// 개발 화면 "배포"·"장애 대응"의 표시 규칙. 순수 계산만 한다(화면·환경 값을 가져오지 않음): `npm test`(node --test)에서 그대로 돈다.
// 값은 서버가 준 것만 쓴다. 배포 도구가 실행하지 않은 배포를 "배포됨"으로, PR 연결을 "완료"로 보이지 않게 한다.
import type { DeployCheck, DeployRun, Deployment } from "./planning";
import type { Incident } from "./planningData";

export type Tone = "green" | "amber" | "neutral";

/** 실제로 나간 배포인가 (배포 도구가 실행함). 예전 서버 응답(executed 없음)은 status로 본다 */
export const deployExecuted = (d: Deployment): boolean => d.executed ?? ["succeeded", "failed", "rolled_back"].includes(d.status);

const STATUS: Record<Deployment["status"], [string, Tone]> = {
  requested: ["승인 대기", "amber"],
  ready: ["배포 실행 대기 (사람이 누름)", "amber"],
  approved: ["승인됨 · 실제 배포 안 함", "neutral"],
  running: ["배포 중 (도구 실행)", "neutral"],
  succeeded: ["배포됨", "green"],
  failed: ["실패", "amber"],
  rolled_back: ["롤백됨", "neutral"],
};

// 승인한 배포의 머리 표시는 실행 기록(run.state)을 따른다 ("실제 배포 안 함"으로 뭉뚱그리지 않는다)
const RUN_HEAD: Partial<Record<DeployRun["state"], [string, Tone]>> = {
  sending: ["보내는 중", "neutral"],
  unknown: ["결과 모름 · 도구에서 확인", "amber"],
  handed: ["사람 할 일함으로 넘어감", "amber"],
  not_sent: ["승인됨 · 보내지 않음", "amber"],
  held: ["안전 확인 대기", "amber"],
  no_tool: ["배포 도구 없음", "amber"],
  rejected: ["관문 거절", "amber"],
  failed: ["보내지 못함", "amber"],
};

export function deployStatusText(d: Deployment): [string, Tone] {
  if (d.status === "succeeded" && !deployExecuted(d)) return STATUS.approved;
  if (d.status === "approved" && d.run) return RUN_HEAD[d.run.state] ?? STATUS.approved;
  return STATUS[d.status] ?? [d.status, "neutral"];
}

/** http(s) 주소만 링크로 쓴다 (서버가 준 값이라도 다른 꼴은 걸지 않는다) */
export const safeUrl = (url: string | null | undefined): string | null => (url && /^https?:\/\//i.test(url) ? url : null);

/** "배포된 PR": 실제로 나가서 끝난 배포에 들어간 PR 수 (머지 수가 아니다. 도구에서 아직 도는 중·실패는 빼고) */
export const deployedPrCount = (rows: Deployment[]): number =>
  new Set(rows.filter((d) => deployExecuted(d) && (d.status === "succeeded" || d.status === "rolled_back")).flatMap((d) => d.pr_numbers)).size;

/** 배포 후 확인에서 못 읽은 신호원이 있으면 도구 연결 링크를 띄운다 */
export const needsToolLink = (c: DeployCheck | null | undefined): boolean => !!c?.signals?.some((x) => x.kind === "unavailable");

/** 사건 카드 상태. linked는 PR·문단에 연결했다는 뜻이고 장애가 복구됐다는 뜻이 아니다 */
export const incidentStageText = (linked: boolean): string => (linked ? "PR 연결됨" : "PR 연결 전");

const REVERT: Record<NonNullable<Incident["revert"]>["status"], string> = {
  merged: "머지됨",
  auto_merge_scheduled: "자동 머지 예약 (CI 대기)",
  held: "안전 확인 대기",
};

export function revertText(r: NonNullable<Incident["revert"]>): string {
  const pr = r.revertPr != null ? ` 되돌리기 PR #${r.revertPr}` : " 되돌리기";
  return `레포 PR #${r.sourcePr}${pr} ${REVERT[r.status] ?? r.status}`;
}

const TOOL_LABEL: Record<string, string> = { github_actions: "GitHub Actions", jenkins: "Jenkins" };
export const toolLabel = (tool: string | null | undefined): string => (tool ? TOOL_LABEL[tool] ?? tool : "배포 도구");

const RUN_STATUS: Record<NonNullable<DeployRun["run_status"]>, [string, Tone]> = {
  dispatched: ["실행 요청됨", "neutral"],
  running: ["실행 중", "neutral"],
  succeeded: ["성공", "green"],
  failed: ["실패", "amber"],
  unknown: ["도구에서 못 찾음", "neutral"],
};

export interface RunView {
  /** 한 줄 요약 (도구 이름·상태 또는 못 보낸 이유) */
  text: string;
  tone: Tone;
  /** 도구 실행 페이지 (서버가 준 주소만) */
  url: string | null;
  /** 배포 도구가 연결되지 않았다: 도구 연결 화면으로 보낸다 */
  needsTool: boolean;
  /** 안전 확인에서 사람 승인을 기다린다 */
  held: boolean;
  /** 도구 실행 상태를 다시 읽을 수 있다 (실행 번호가 있음) */
  canRefresh: boolean;
  /** 승인했지만 보내지 않았다: '배포 실행' 버튼을 보인다 */
  canSend: boolean;
}

export const NO_TOOL_TEXT = "배포 도구가 연결되지 않았어요 — 도구 연결에서 설정";

/** 배포 실행 기록을 화면 한 줄로. 없으면 null (보내기 전·예전 응답) */
export function deployRunView(run: DeployRun | null | undefined): RunView | null {
  if (!run) return null;
  const base = { url: null, needsTool: false, held: false, canRefresh: false, canSend: false };
  const why = run.reason ?? "";
  switch (run.state) {
    case "dispatched": {
      const [st, tone]: [string, Tone] = run.resolving ? ["도구에서 확인 중", "neutral"] : RUN_STATUS[run.run_status ?? "dispatched"] ?? RUN_STATUS.dispatched;
      const num = run.run_id ? ` 실행 ${run.run_id}` : " (실행 번호 모름)";
      return { ...base, text: `${toolLabel(run.tool)}${num} · ${st}`, tone, url: safeUrl(run.url), canRefresh: !!run.run_id };
    }
    case "sending":
      return { ...base, text: "배포 도구로 보내는 중", tone: "neutral" };
    case "no_tool":
      return { ...base, text: NO_TOOL_TEXT, tone: "amber", needsTool: true };
    case "held":
      return { ...base, text: why || "보류 → 안전 확인에서 승인하면 이어서 배포해요", tone: "amber", held: true };
    case "rejected":
      return { ...base, text: why || "안전 관문이 거절해 배포하지 않았어요", tone: "amber" };
    case "unknown":
      return { ...base, text: why || "배포가 시작됐을 수 있어요 — 배포 도구에서 먼저 확인", tone: "amber", url: safeUrl(run.url) };
    case "handed":
      return { ...base, text: why || "사람 할 일함으로 넘어감 — 사람이 실행", tone: "amber" };
    case "not_sent":
      return { ...base, text: why || "승인했지만 배포 도구로 보내지 않았어요", tone: "amber", canSend: true };
    default:
      return { ...base, text: why || "배포하지 못했어요", tone: "amber" };
  }
}
