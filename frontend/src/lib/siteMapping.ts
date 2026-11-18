// 다른 형식 API 연결의 짝(사이트 변환기 mapping.json 모양)과 화면용 순수 함수. node --test로 시험(siteMapping.test.ts).
// 런타임 import 없이 둔다(node가 확장자 없는 import를 못 찾는다).

export type SiteResource = "orders" | "products";

export interface ResourceMapping {
  path: string;
  paging?: Record<string, unknown>;
  /** shop-api 칸 → 고객사 짝 식(점 경로 · `sum:a,b` · `const:x`). 못 찾은 칸은 "" */
  fields: Record<string, string>;
  /** 칸 값 바꾸기 표(주문 상태: 고객사 값 → pending_payment·paid·cancelled) */
  values?: Record<string, Record<string, string>>;
}

export interface SiteMapping {
  version: 1;
  auth_header: string;
  auth_scheme: string;
  resources: Record<SiteResource, ResourceMapping>;
}

/** 첫 줄에서 짝 식의 값을 보여 준다(점 경로만. 합·고정값은 식 그대로, 없는 경로는 "없는 칸") */
export function preview(row: Record<string, unknown> | null | undefined, expr: string): string {
  if (!row || !expr) return "—";
  if (expr.startsWith("sum:") || expr.startsWith("const:")) return expr;
  let value: unknown = row;
  for (const key of expr.split(".")) {
    if (value === null || typeof value !== "object" || !(key in value)) return "없는 칸";
    value = (value as Record<string, unknown>)[key];
  }
  const text = value === null ? "null" : typeof value === "object" ? JSON.stringify(value) : String(value);
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}

/** 아직 정하지 않은 칸(빈 짝 식·정하지 않은 주문 상태). `자원.칸` / `orders.status=<값>` */
export function unfilled(mapping: SiteMapping): string[] {
  const out: string[] = [];
  for (const res of ["orders", "products"] as const) {
    const r = mapping.resources[res];
    for (const [name, expr] of Object.entries(r.fields)) if (!expr.trim()) out.push(`${res}.${name}`);
  }
  for (const [raw, to] of Object.entries(mapping.resources.orders.values?.status ?? {})) {
    if (!to) out.push(`orders.status=${raw}`);
  }
  return out;
}
