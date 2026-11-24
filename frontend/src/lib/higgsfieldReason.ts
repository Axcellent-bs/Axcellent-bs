// 마케팅 › SNS·도구 계정 카드의 Higgsfield 줄: 실패 사유별 표시 (11/24, 화면 시안).
// 순수 계산만 한다: `npm test`(node --test)에서 그대로 돈다.
// 서버(higgsfield_status)가 reason_code(사유 코드)와 detail_ko(Higgsfield 문장, 주소·토큰 가림)를 준다.
// reason_code가 없으면(옛 서버·성공) 지금 표시 그대로다.
export type ReasonTone = "bad" | "wait";

/** 사유 코드 → 칩 (시안 표 그대로). 모르는 코드는 null (지금 표시 그대로) */
export function reasonView(code: string | null | undefined): { tone: ReasonTone; label: string } | null {
  if (!code) return null;
  if (code === "network" || code === "timeout") return { tone: "wait", label: "닿지 않음" };
  if (/^http_\d+$/.test(code)) return { tone: "wait", label: "서버 오류" };
  if (code === "refresh_failed") return { tone: "wait", label: "토큰 갱신 실패" };
  if (code === "tool_error") return { tone: "bad", label: "도구 오류" };
  if (code === "bad_response" || code === "rpc_error") return { tone: "bad", label: "응답 형식 오류" };
  if (code === "client_error") return { tone: "bad", label: "요청 만들기 실패" };
  if (code === "reconnect_needed" || code === "not_connected" || code === "unauthorized") return { tone: "bad", label: "다시 연결 필요" };
  return null;
}

/** 확인 시각 "11/24 15:17" (한국 시각). 읽지 못하면 빈 글 */
export function checkedAt(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("month")}/${get("day")} ${get("hour")}:${get("minute")}`;
}

/** 아래 줄 "사유 tool_error · 확인 11/24 15:17". 사유가 없으면 null */
export function reasonLine(code: string | null | undefined, iso: string | null | undefined): string | null {
  if (!code) return null;
  const at = checkedAt(iso);
  return at ? `사유 ${code} · 확인 ${at}` : `사유 ${code}`;
}
