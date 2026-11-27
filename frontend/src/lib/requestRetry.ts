// 의뢰 단계 다시 시도 (11/27). node --test (requestRetry.test.ts)
// 총괄 retry_step과 같은 규칙: 실패 · 영상 거절/기한 지남 · 앞 단계 결과가 없어 막힘만 다시 시도할 수 있다.
// 앞 단계 결과는 그대로 두고 그 단계와 뒤 단계만 다시 돈다. 단계마다 3번까지.
import { requestActionError } from "./requestCards.ts";
import type { WorkRequestStep } from "./workRequests.ts";

export const RETRY_MAX = 3;
export const RETRY_LABEL = "이 단계부터 다시";
/** 기존 [이 일로 다시 돌리기]: 계획부터 새로 (보고서·제안을 다시 만든다) */
export const FULL_RERUN_LABEL = "처음부터 다시(새 제작안)";
export const RETRY_LIMIT_KO = `다시 시도를 ${RETRY_MAX}번 했어요. 처음부터 다시 돌려 주세요`;

const REJECTED = new Set(["video_rejected"]);
const SKIPPED = new Set(["no_insight", "no_carry"]);

type StepLike = Pick<WorkRequestStep, "status" | "reason_code"> & { retry_count?: number | null };

export function canRetryStep(s: StepLike | null | undefined): boolean {
  if (!s) return false;
  const code = s.reason_code ?? "";
  return s.status === "failed" || (s.status === "rejected_by_human" && REJECTED.has(code)) || (s.status === "skipped" && SKIPPED.has(code));
}

export const retriesOf = (s: StepLike | null | undefined): number => Math.max(0, Number(s?.retry_count ?? 0) || 0);

/** "다시 시도 1/3" (한 번도 안 했으면 null) */
export function retryCountLine(s: StepLike | null | undefined): string | null {
  const n = retriesOf(s);
  return n > 0 ? `다시 시도 ${n}/${RETRY_MAX}` : null;
}

/** 상한을 넘으면 버튼 대신 안내 */
export const retryLimited = (s: StepLike | null | undefined): boolean => retriesOf(s) >= RETRY_MAX;

const num = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));

/** 버튼 글: 비용 드는 단계는 크레딧 예상과 승인 (알고 있는 값만, 모르면 빼고 "승인 필요"만) */
export function retryButton(skill: string, credits?: number | null): string {
  if (skill === "generate_media") {
    const cost = typeof credits === "number" && credits > 0 ? `영상 약 ${num(credits)} 크레딧 · ` : "영상 ";
    return `${RETRY_LABEL} · ${cost}승인 필요`;
  }
  if (skill === "prepare_video_draft_from_plan") return `${RETRY_LABEL} · 스토리보드 그림 없으면 약 1.5 크레딧`;
  return RETRY_LABEL;
}

/** 다시 시도 실패 → 한 줄. 422(상한·실패한 단계 아님)는 서버 문장, 409는 진행 중 */
export function retryError(e: unknown): { text: string; reload: boolean } {
  const status = typeof (e as { status?: unknown } | null)?.status === "number" ? (e as { status: number }).status : null;
  const message = e instanceof Error ? e.message : "";
  if (status === 422 && message) return { text: message, reload: true };
  if (status === 409) return { text: "진행 중이거나 기다리는 단계가 있어 지금은 다시 시도할 수 없어요", reload: true };
  return requestActionError(e);
}
