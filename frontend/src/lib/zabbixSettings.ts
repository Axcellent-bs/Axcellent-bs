// 회원·서버 관리 › DejaFix 연결 › "Zabbix 연결"의 화면 없는 함수(node --test로 시험). 부르는 쪽은 lib/dejafix.ts

/** DejaFix 계약 #28 `GET /api/settings/zabbix`: 같이 설치된 DejaFix가 보는 Zabbix. 토큰 값은 오지 않는다 */
export interface ZabbixSettings {
  /** file = 화면에서 정함 · env = 서버 설치 설정(.env) · none = 없음 */
  source: "file" | "env" | "none";
  url: string | null;
  token_set: boolean;
  verify_tls: boolean;
  updated_at: string | null;
  updated_by: string | null;
  /** DejaFix가 목 데이터를 쓰는 중(개발 설정) */
  mock: boolean;
}

/** 응답 모양 검사. 모르는 모양이면 null (지어내지 않는다) */
export function parseZabbixSettings(body: unknown): ZabbixSettings | null {
  const d = (body as { data?: unknown } | null)?.data;
  if (!d || typeof d !== "object") return null;
  const x = d as Record<string, unknown>;
  if (x.source !== "file" && x.source !== "env" && x.source !== "none") return null;
  const str = (v: unknown) => (typeof v === "string" && v ? v : null);
  return {
    source: x.source,
    url: str(x.url),
    token_set: x.token_set === true,
    verify_tls: x.verify_tls !== false,
    updated_at: str(x.updated_at),
    updated_by: str(x.updated_by),
    mock: x.mock === true,
  };
}

/** 지금 연결 한 줄 ("화면에서 정함 · https://… · 토큰 있음") */
export function zabbixSummary(z: ZabbixSettings): string {
  if (z.source === "none") return "Zabbix가 아직 연결되지 않았어요";
  const from = z.source === "file" ? "화면에서 정함" : "서버 설치 설정";
  return [from, z.url ?? "주소 없음", z.token_set ? "토큰 있음" : "토큰 없음"].join(" · ");
}
