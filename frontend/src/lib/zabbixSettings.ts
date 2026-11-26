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

/**
 * 토큰이 보호되지 않는 연결이면 경고 한 줄 (DejaFix 리뷰 11/26). 없으면 null.
 * http:// 는 토큰이 평문으로 가고, 인증서 확인을 끄면 중간에서 가로챌 수 있다.
 */
export function zabbixInsecure(url: string, verifyTls: boolean): string | null {
  const plain = /^\s*http:\/\//i.test(url);
  if (plain) return "http:// 주소라 토큰이 암호화되지 않고 그대로 가요. 가능하면 https:// 주소를 쓰세요.";
  if (!verifyTls && /^\s*https:\/\//i.test(url)) return "인증서 확인을 끄면 중간에서 토큰을 가로챌 수 있어요. 사내 자체 인증서일 때만 끄세요.";
  return null;
}

/** `/settings/zabbix` 호출이 HTTP 오류로 끝났을 때. 상태 코드와 프록시 오류 코드(error.code)를 함께 들고 있다 */
export class ZabbixCallError extends Error {
  status: number;
  code: string | null;
  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = "ZabbixCallError";
    this.status = status;
    this.code = code;
  }
}

/**
 * 옛 DejaFix라 화면에서 Zabbix를 바꾸지 못하는 경우인가. 프록시가 옛 DejaFix의 404·405를
 * 501 DEJAFIX_TOO_OLD로 바꿔 준다. 이때만 true — 502·409·401·403 등 다른 오류는 false.
 */
export function zabbixUnsupported(err: unknown): boolean {
  return err instanceof ZabbixCallError && (err.status === 501 || err.code === "DEJAFIX_TOO_OLD");
}
