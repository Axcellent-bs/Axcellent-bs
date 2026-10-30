// 해결 시간 표시. 서버는 분 단위(내림)로 저장하지만 열린 시각과 해결한 시각이 같이 남아 있어서 초까지 보여 줄 수 있다.
export interface ResolutionTimes {
  opened_at?: string | null;
  resolved_at?: string | null;
  minutes_to_resolve?: number | null;
}

/** 열린 시각~해결한 시각의 초. 시각이 없으면 분 값으로, 그것도 없으면 null */
export function secondsOf(r: ResolutionTimes | null | undefined): number | null {
  if (!r) return null;
  if (r.opened_at && r.resolved_at) {
    const sec = Math.round((Date.parse(r.resolved_at) - Date.parse(r.opened_at)) / 1000);
    if (Number.isFinite(sec) && sec >= 0) return sec;
  }
  return typeof r.minutes_to_resolve === "number" ? r.minutes_to_resolve * 60 : null;
}

/** 45초 · 2분 5초 · 3분 */
export function durationKo(seconds: number): string {
  const s = Math.max(Math.round(seconds), 0);
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m === 0) return `${rest}초`;
  return rest === 0 ? `${m}분` : `${m}분 ${rest}초`;
}
