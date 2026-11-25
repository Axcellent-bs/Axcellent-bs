// 화면이 쓰는 사람 토큰과 "쓰기 버튼을 켤지"를 한곳에서 살아 있는 값으로 둔다(env.ts가 다시 내보낸다).
// - 사람 토큰: 만료 전 로그인 세션이 있으면 세션 토큰, 없으면 공용 토큰(humanSession.ts pickHumanToken)
// - 로그인·로그아웃(onSessionChange)마다 둘 다 다시 고른다. ES 모듈의 살아 있는 바인딩이라 읽는 곳은 늘 지금 값을 본다
// - 켜짐·꺼짐이 바뀌면 onHumanTokenChange로 알린다. 화면은 useHumanTokenSet()으로 다시 그린다
// 공용 토큰을 꺼도(빈 값) 로그인한 사람은 새로고침 없이 버튼이 켜진다.
// import.meta.env를 읽지 않아 node --test로 그대로 돈다(공용 토큰 값은 env.ts가 setSharedHumanToken으로 넣는다).
import { getSession, onSessionChange, pickHumanToken } from "./humanSession.ts";

let shared = "";
/** 쓰기·읽기에 붙이는 사람 토큰. 비어 있으면 붙이지 않는다 */
export let SAFETY_HUMAN_TOKEN = "";
/** 사람 토큰이 있는지(쓰기 버튼을 켤지). 없으면 쓰기 버튼을 끄고 "로그인이 필요해요"라고 안내한다 */
export let HUMAN_TOKEN_SET = false;

const listeners = new Set<() => void>();

function refresh(): void {
  const before = HUMAN_TOKEN_SET;
  SAFETY_HUMAN_TOKEN = pickHumanToken(getSession(), shared);
  HUMAN_TOKEN_SET = SAFETY_HUMAN_TOKEN.length > 0;
  if (before !== HUMAN_TOKEN_SET) listeners.forEach((l) => l());
}

/** 공용 사람 토큰(번들 값·자리표시자). env.ts가 처음 한 번 넣는다. 시험에서도 바꿔 끼운다 */
export function setSharedHumanToken(t: string): void {
  shared = t;
  refresh();
}

/**
 * 로그인 세션이 있을 때만 그 세션 토큰을 싣는 머리(읽기용). 세션이 없으면 비워서 지금처럼 보낸다
 * (같은 출처 nginx가 빈 Authorization에 공용 토큰을 붙이는 동안은 그대로 동작하고, 그 길을 끄면 로그인한 사람만 읽는다).
 */
export function sessionAuth(): Record<string, string> {
  const s = getSession();
  return s ? { Authorization: `Bearer ${s.token}` } : {};
}

/** 회사 고르기용 세션 머리 이름. 서버는 Authorization이 세션 토큰이 아니면 이 머리로 회사만 고른다(권한은 Authorization) */
export const SESSION_HEADER = "X-AX-Session";

/**
 * 읽기(GET)용 머리: 세션이 있으면 Authorization과 X-AX-Session 둘 다 싣는다(11/25 회사 분리).
 * 같은 출처 nginx는 읽기의 Authorization을 공용 토큰으로 바꿔 붙여서, 서버가 세션 회사를 못 보고 캣북스로 읽었다.
 * 직접 호스트(apiDirect)로 갈 때는 거기서 X-AX-Session을 뗀다(직접 호스트는 바꿔 붙이지 않고 CORS가 이 머리를 허용하지 않는다).
 */
export function sessionReadAuth(): Record<string, string> {
  const s = getSession();
  return s ? { Authorization: `Bearer ${s.token}`, [SESSION_HEADER]: s.token } : {};
}

export const humanTokenSetNow = (): boolean => HUMAN_TOKEN_SET;

/** 쓰기 버튼 켜짐·꺼짐이 바뀔 때 부른다. 돌려준 함수로 그만 듣는다 */
export function onHumanTokenChange(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

onSessionChange(refresh);
refresh();
