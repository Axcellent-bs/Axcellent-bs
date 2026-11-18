// 화면이 서버에 보내는 읽기(GET) 요청 두 가지 규칙. 화면 코드(api.ts)가 쓴다.
// 1) 같은 주소를 부르는 요청이 이미 오는 중이면 새로 보내지 않고 그 답을 같이 받는다(문의 목록처럼 여러 곳이 동시에 부르는 경우).
//    저장해 두는 것이 아니라 "오는 중인 동안"만 같이 받는다. 답이 오면 바로 비우므로 오래된 값을 돌려주지 않는다.
// 2) 서버가 답하지 않을 때 영원히 기다리지 않는다(시간 제한). 쓰기(승인·환불 같은 POST)에는 걸지 않는다(두 번 처리될 수 있어서).

export const REQUEST_TIMEOUT_MS = 30_000;
export const TIMEOUT_MESSAGE = "서버가 30초 안에 답하지 않았습니다. 잠시 뒤 다시 시도해 주세요.";

const inflight = new Map<string, Promise<unknown>>();

/** key가 같은 요청이 오는 중이면 그 약속을 같이 쓰고, 아니면 run을 실행한다. 끝나면(성공·실패 모두) 비운다 */
export function shareInflight<T>(key: string, run: () => Promise<T>): Promise<T> {
  const running = inflight.get(key);
  if (running) return running as Promise<T>;
  const p = run().finally(() => {
    if (inflight.get(key) === p) inflight.delete(key);
  });
  inflight.set(key, p);
  return p;
}

/** 같이 받은 답을 호출마다 따로 복사한다(한 곳이 목록을 정렬·수정해도 다른 곳이 영향받지 않게) */
export const copyShared = <T>(value: T): T => (value === undefined ? value : structuredClone(value));

/** ms 뒤에 끊기는 신호 */
export function timeoutSignal(ms = REQUEST_TIMEOUT_MS): AbortSignal {
  if (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal) return AbortSignal.timeout(ms);
  const c = new AbortController();
  setTimeout(() => c.abort(new DOMException("timeout", "TimeoutError")), ms);
  return c.signal;
}

export const isTimeoutError = (e: unknown): boolean =>
  typeof e === "object" && e !== null && (e as { name?: string }).name === "TimeoutError";

/** 오는 중인 요청을 함께 받아도 되는 읽기인지: 방식·본문·머리글·신호를 따로 정하지 않은 GET만 */
export function isShareableGet(init?: RequestInit): boolean {
  if (!init) return true;
  const m = (init.method ?? "GET").toUpperCase();
  return m === "GET" && init.body == null && init.headers == null && init.signal == null;
}

/** 테스트용: 오는 중인 요청을 모두 비운다 */
export function _resetInflight(): void {
  inflight.clear();
}
