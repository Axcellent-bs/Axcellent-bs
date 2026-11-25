// 화면의 읽기(GET) API 를 같은 출처 nginx 프록시 대신 외부 직접 호스트로 보내는 길 (경로 단축).
// docs/ops/api-direct/PATHS.md 가 근거 문서이고, 서버(외부용 nginx)가 허용하는 경로와 아래 표가 같아야 한다.
//
// 켜는 법: 환경값 두 개가 모두 있어야 한다. 하나라도 비면 이 모듈은 아무것도 하지 않는다(지금과 완전히 같다).
//   VITE_API_DIRECT_BASE      직접 호스트 주소(출처만, 예: https://api-app.axcellent.cloud). 비우면 꺼짐
//   VITE_API_DIRECT_SERVICES  직접 보낼 서비스 목록(쉼표, 예: dashboard,orch,safety). 비우면 꺼짐
// 서버가 준비되기 전에 배포돼도 안전하도록 기본값은 꺼짐이다.
//
// 보내는 규칙
// - GET 만, 아래 표(DIRECT_ROUTES)에 있는 경로만. 쓰기(POST·PUT·PATCH·DELETE)와 관리자 경로는 같은 출처 그대로
// - 직접 호스트는 토큰을 대신 붙여 주지 않는다(공용 사람 토큰 주입은 같은 출처 nginx 만 한다).
//   그래서 토큰이 필요한 경로는 로그인 세션 토큰(axs1.)이 있을 때만 직접 보내고, 헤더에 없으면 세션 토큰을 실어 준다.
//   세션이 없으면(자리표시자만 있으면) 같은 출처 그대로 간다
// - 토큰이 필요 없는 경로(총괄 이슈·안전 실행 기록·기획/개발 state)는 헤더를 건드리지 않는다.
//   Authorization 이 없으면 단순 요청이라 브라우저가 OPTIONS 사전 요청을 보내지 않는다
// - 직접 호스트에 닿지 못하면(네트워크·CORS 실패) 같은 출처로 한 번 다시 보내고, 잠시(60초) 직접 길을 쉰다
import { getSession } from "./humanSession.ts";

/** 직접 보낼 수 있는 서비스. 값은 같은 출처 nginx 의 경로 접두(직접 호스트도 같은 접두를 쓴다) */
export const DIRECT_PREFIX = {
  dashboard: "/dashboard-api",
  orch: "/orch",
  safety: "/safety-api",
  planning: "/planning-api",
  dev: "/dev-api",
} as const;
export type DirectService = keyof typeof DIRECT_PREFIX;

/** session: 로그인 세션 토큰 필수, none: 서버가 토큰을 안 본다(헤더를 건드리지 않는다) */
export type DirectAuth = "session" | "none";
export interface DirectRoute { service: DirectService; auth: DirectAuth; path: RegExp }

/**
 * 1차 허용 표: 로그인 직후 첫 화면(AI 대화방 + 사이드바)이 부르는 읽기. 정규식은 접두 뒤 경로 전체(쿼리 제외)에 맞는다.
 * 경로를 늘릴 때는 PATHS.md 와 서버 허용 목록을 같이 고친다. 쓰기 경로·관리자 경로는 넣지 않는다.
 */
export const DIRECT_ROUTES: readonly DirectRoute[] = [
  // dashboard-api: 로그인 전 공개 두 개는 토큰을 보지 않는다
  { service: "dashboard", auth: "none", path: /^\/api\/dashboard\/auth\/(status|company)$/ },
  { service: "dashboard", auth: "session", path: /^\/api\/dashboard\/auth\/me$/ },
  { service: "dashboard", auth: "session", path: /^\/api\/dashboard\/(communications|office-room|handoffs|connection-keys|company-connection)$/ },
  { service: "dashboard", auth: "session", path: /^\/api\/dashboard\/shop\/(banners|faults)$/ },
  // 총괄·안전 관문: 이 읽기는 서버가 토큰을 보지 않는다
  { service: "orch", auth: "none", path: /^\/api\/incidents$/ },
  { service: "safety", auth: "none", path: /^\/api\/safety\/executions$/ },
  // 기획·개발 state (사이드바 숫자, 30초마다). 세션 회사로 그 회사 상태를 고르므로 세션 토큰을 싣는다 (11/25 회사 분리)
  { service: "planning", auth: "session", path: /^\/api\/planning\/state$/ },
  { service: "dev", auth: "session", path: /^\/api\/planning\/state$/ },
];

export interface DirectConfig { base: string; services: ReadonlySet<DirectService> }

type Env = Record<string, unknown>
const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");

/** 환경값 읽기. 주소가 없거나 모양이 틀리거나 서비스가 하나도 없으면 null(꺼짐) */
export function readDirectConfig(env: Env): DirectConfig | null {
  const raw = str(env.VITE_API_DIRECT_BASE);
  if (!raw) return null;
  let u: URL;
  try { u = new URL(raw); } catch { return null; }
  const local = u.hostname === "localhost" || u.hostname === "127.0.0.1";
  if (u.protocol !== "https:" && !(local && u.protocol === "http:")) return null;
  // 출처만. 경로·쿼리·인증정보가 붙어 있으면 잘못 적은 것이라 켜지 않는다
  if ((u.pathname !== "/" && u.pathname !== "") || u.search || u.hash || u.username || u.password) return null;
  const services = new Set<DirectService>();
  for (const s of str(env.VITE_API_DIRECT_SERVICES).split(",").map((x) => x.trim()).filter(Boolean)) {
    if (s in DIRECT_PREFIX) services.add(s as DirectService);
  }
  return services.size ? { base: u.origin, services } : null;
}

export interface RouteInput {
  url: unknown;
  method: string;
  /** 요청이 이미 가진 Authorization 헤더 값(없으면 null) */
  authorization: string | null;
  /** 지금 로그인 세션 토큰(없으면 null) */
  sessionToken: string | null;
  cfg: DirectConfig;
}
export interface RouteResult {
  url: string;
  /** 헤더에 새로 실을 Authorization 값. 없으면 헤더를 건드리지 않는다 */
  authorization?: string;
}

const SESSION_PREFIX = "axs1.";
const bearerOf = (h: string | null) => /^Bearer\s+(\S+)$/i.exec(h?.trim() ?? "")?.[1] ?? "";

/** 이 요청을 직접 호스트로 보낼 수 있으면 새 주소(와 실을 헤더)를, 아니면 null */
export function routeDirect(i: RouteInput): RouteResult | null {
  if (typeof i.url !== "string" || !i.url.startsWith("/") || i.url.startsWith("//")) return null;
  const m = i.method.toUpperCase();
  if (m !== "GET") return null;
  const q = i.url.indexOf("?");
  const pathname = q < 0 ? i.url : i.url.slice(0, q);
  for (const svc of i.cfg.services) {
    const prefix = DIRECT_PREFIX[svc];
    if (!pathname.startsWith(`${prefix}/`)) continue;
    const rest = pathname.slice(prefix.length);
    const rule = DIRECT_ROUTES.find((r) => r.service === svc && r.path.test(rest));
    if (!rule) return null;
    const url = `${i.cfg.base}${i.url}`;
    if (rule.auth === "none") return { url };
    const have = bearerOf(i.authorization);
    if (have.startsWith(SESSION_PREFIX)) return { url };
    if (!have && i.sessionToken?.startsWith(SESSION_PREFIX)) return { url, authorization: `Bearer ${i.sessionToken}` };
    return null;
  }
  return null;
}

/**
 * 서버가 토큰을 보지 않는 읽기인지(위 표의 auth "none"). 이런 읽기에는 Authorization을 싣지 않는다
 * (헤더가 없어야 단순 요청이라 직접 호스트로 갈 때 OPTIONS 사전 요청이 생기지 않는다). path는 접두 뒤 경로(쿼리 무시)
 */
export function tokenFree(service: DirectService, path: string): boolean {
  const q = path.indexOf("?");
  const p = q < 0 ? path : path.slice(0, q);
  return DIRECT_ROUTES.some((r) => r.service === service && r.auth === "none" && r.path.test(p));
}

/** 직접 길 쉬는 시간(실패 뒤) */
export const REST_MS = 60_000;

export interface InstallDeps {
  fetch?: typeof fetch;
  now?: () => number;
  sessionToken?: () => string | null;
}

/** 전역 fetch 를 바꿔 끼운다. 꺼져 있으면(cfg null) 아무것도 바꾸지 않고 null. 돌려주는 함수가 원래대로 되돌린다 */
export function installApiDirect(cfg: DirectConfig | null, deps: InstallDeps = {}): (() => void) | null {
  if (!cfg) return null;
  const original = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const now = deps.now ?? Date.now;
  const token = deps.sessionToken ?? (() => getSession()?.token ?? null);
  let restUntil = 0;
  const wrapped = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input !== "string" || now() < restUntil) return original(input, init);
    const headers = new Headers(init?.headers);
    const r = routeDirect({ url: input, method: init?.method ?? "GET", authorization: headers.get("authorization"), sessionToken: token(), cfg });
    if (!r) return original(input, init);
    let next = init;
    if (r.authorization) {
      headers.set("Authorization", r.authorization);
      next = { ...init, headers };
    }
    try {
      return await original(r.url, next);
    } catch (e) {
      // 네트워크·CORS 실패(TypeError)만 같은 출처로 다시 보낸다. 중단·시간 제한은 그대로 던진다
      if (!(e instanceof TypeError)) throw e;
      restUntil = now() + REST_MS;
      return original(input, init);
    }
  }) as typeof fetch;
  globalThis.fetch = wrapped;
  return () => { globalThis.fetch = original; };
}
