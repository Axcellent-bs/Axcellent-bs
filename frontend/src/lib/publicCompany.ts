// 로그인 전 화면이 읽는 회사 공개 정보 (dashboard-api `GET /api/dashboard/auth/company`, 토큰 없이 열린 경로).
// dashboard-api는 읽기에도 사람 토큰을 요구해서 로그인 전에는 연결 키 목록·회사 연결 상태를 읽지 못한다.
// 그런데 로그인 화면은 "회사 연결 전에는 관리자만 로그인"을 정하려고 연결 회사가 필요하다(demo/LoginPage.tsx, session.tsx canOpen).
// 그래서 회사 id·이름·사이트 주소만 따로 받는다. 순수 계산이라 `npm test`가 그대로 돈다.

export interface PublicCompany {
  id: string;
  name: string;
  /** 회사 사이트 주소. 없으면 undefined */
  siteUrl?: string;
}

/** `{data: {company: {id, name, site_url} | null}}`를 읽는다. 모양이 틀리면 null(회사 없음으로 본다) */
export function readPublicCompany(body: unknown): PublicCompany | null {
  const c = (body as { data?: { company?: unknown } } | null | undefined)?.data?.company;
  if (!c || typeof c !== "object") return null;
  const o = c as Record<string, unknown>;
  if (typeof o.name !== "string" || !o.name.trim()) return null;
  const id = typeof o.id === "string" && o.id ? o.id : `public-${o.name.trim()}`;
  const siteUrl = typeof o.site_url === "string" && /^https?:\/\//.test(o.site_url) ? o.site_url : undefined;
  return { id, name: o.name.trim(), ...(siteUrl ? { siteUrl } : {}) };
}

/**
 * 토큰이 없거나 받지 않는 사람이라 거절됐나(401·403). 그러면 지난 사람이 읽은 값(키 목록 등)을 지운다.
 * 연결 실패·서버 오류(그 밖)는 지난 값을 그대로 둔다.
 */
export function refusedForThisPerson(e: unknown): boolean {
  const status = (e as { status?: unknown } | null | undefined)?.status;
  return status === 401 || status === 403;
}

/** 화면이 쓰는 연결 회사: 연결 키로 연결한 회사 → 사이트 연결 회사 → 로그인 전 공개 정보 순서 */
export function pickCompany<T>(keyed: T | null, site: T | null, pub: T | null): T | null {
  return keyed ?? site ?? pub;
}
