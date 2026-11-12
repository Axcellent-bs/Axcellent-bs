// 누가 했나 표시용 화면 계정 이름. 지금은 모든 화면이 사람 토큰 하나("공용")를 같이 써서
// 서버 기록이 "공용"으로만 남는다. 그래서 사람 결정 요청에 로그인한 계정 이름을 같이 보내고,
// 서버가 "관리자(192.168.0.22)"처럼 접속 IP와 묶어 `by_label`로 남긴다(재무 human.py).
// 권한은 여전히 토큰으로만 정한다. 이 이름은 표시용이다.

export const ACTOR_HEADER = "X-Ax-Actor-Name";
const MAX = 40;

let current: string | null = null;

/** 로그인 계정이 바뀌면 SessionProvider가 부른다 */
export function setActorName(name: string | null | undefined): void {
  // 제어 문자·괄호는 빼서 "이름(IP)" 모양을 지킨다 (서버도 같은 규칙으로 한 번 더 거른다)
  const clean = (name ?? "").replace(/[\u0000-\u001f\u007f()<>]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX).trim();
  current = clean || null;
}

/** 요청 머리. 머리 값은 ASCII만 되므로 UTF-8 퍼센트 인코딩해 보낸다 */
export function actorHeaders(): Record<string, string> {
  return current ? { [ACTOR_HEADER]: encodeURIComponent(current) } : {};
}

/** 기록에 보일 이름: 서버가 붙인 표시 이름, 없으면 토큰 이름 */
export function actorText(label: string | null | undefined, by: string | null | undefined, fallback = "사람"): string {
  return label || by || fallback;
}
