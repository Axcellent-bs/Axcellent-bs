// 개발 화면 "배포"·"장애 대응"의 표시 규칙. 순수 계산만 한다(화면·환경 값을 가져오지 않음): `npm test`(node --test)에서 그대로 돈다.
// 값은 서버가 준 것만 쓴다. 배포 도구가 실행하지 않은 배포를 "배포됨"으로, PR 연결을 "완료"로 보이지 않게 한다.
import type { DeployCheck, Deployment } from "./planning";

export type Tone = "green" | "amber" | "neutral";

/** 실제로 나간 배포인가 (배포 도구가 실행함). 예전 서버 응답(executed 없음)은 status로 본다 */
export const deployExecuted = (d: Deployment): boolean => d.executed ?? ["succeeded", "failed", "rolled_back"].includes(d.status);

const STATUS: Record<Deployment["status"], [string, Tone]> = {
  requested: ["승인 대기", "amber"],
  approved: ["승인됨 · 실제 배포 안 함", "neutral"],
  running: ["배포 중", "neutral"],
  succeeded: ["배포됨", "green"],
  failed: ["실패", "amber"],
  rolled_back: ["롤백됨", "neutral"],
};

export function deployStatusText(d: Deployment): [string, Tone] {
  if (d.status === "succeeded" && !deployExecuted(d)) return STATUS.approved;
  return STATUS[d.status] ?? [d.status, "neutral"];
}

/** "배포된 PR": 실제로 나간 배포에 들어간 PR 수 (머지 수가 아니다) */
export const deployedPrCount = (rows: Deployment[]): number => new Set(rows.filter(deployExecuted).flatMap((d) => d.pr_numbers)).size;

/** 배포 후 확인에서 못 읽은 신호원이 있으면 도구 연결 링크를 띄운다 */
export const needsToolLink = (c: DeployCheck | null | undefined): boolean => !!c?.signals?.some((x) => x.kind === "unavailable");

/** 사건 카드 상태. linked는 PR·문단에 연결했다는 뜻이고 장애가 복구됐다는 뜻이 아니다 */
export const incidentStageText = (linked: boolean): string => (linked ? "PR 연결됨" : "PR 연결 전");
