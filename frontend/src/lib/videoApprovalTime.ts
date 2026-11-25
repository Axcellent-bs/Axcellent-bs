// 영상 승인 기한 (11/26). 안전 관문 보류 건의 마감(`expires_at`)으로 남은 시간을 보여 주고,
// 기한이 지나 자동 취소된 영상(`render.video.reason === "review_timeout"`)은 같은 기록으로 다시 승인에 올린다.
// 기한 값은 safety 기준표(thresholds.toml `[review_timeout_by_action]`, 영상·썸네일 1시간)가 정한다.

export const TIMED_OUT = "review_timeout";

/** 남은 초. 마감을 모르거나 날짜가 아니면 null */
export function secondsLeft(expiresAt: string | null | undefined, nowMs: number): number | null {
  if (!expiresAt) return null;
  const t = new Date(expiresAt).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((t - nowMs) / 1000);
}

/** "52분 남음" · "23시간 52분 남음" · "1분 미만 남음" · "기한 지남". 모르면 null(칩은 "승인 대기"만) */
export function leftKo(seconds: number | null): string | null {
  if (seconds == null) return null;
  if (seconds <= 0) return "기한 지남";
  if (seconds < 60) return "1분 미만 남음";
  const minutes = Math.floor(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}분 남음`;
  return m === 0 ? `${h}시간 남음` : `${h}시간 ${m}분 남음`;
}

/** 승인 대기 칩 문구 */
export function approvalChipKo(seconds: number | null): string {
  const left = leftKo(seconds);
  return left ? `승인 대기 · ${left}` : "승인 대기";
}

/** 기한이 지나 자동 취소된 영상인지(사람이 거절한 건은 아니다) */
export function timedOut(video: { status?: string; reason?: string | null } | null | undefined): boolean {
  return video?.status === "rejected" && video.reason === TIMED_OUT;
}
