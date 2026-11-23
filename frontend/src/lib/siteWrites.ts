// 회사 연결 › 사이트 쓰기 (사이트 변환기 ③-쓰기 W4)의 짝 모양과 화면용 순수 함수. node --test로 시험(siteWrites.test.ts).
// 런타임 import 없이 둔다(node가 확장자 없는 import를 못 찾는다). 서버 검사는 dashboard-api `mapping.WriteMap`이 정답이고,
// 여기 검사는 저장 전에 빨리 알려 주는 용도다.

export type WriteAction =
  | "refund" | "coupon_issue" | "banner_post" | "banner_end"
  | "claim_result" | "inquiry_answer" | "payment_rollback" | "fault_release";

export type WriteMethod = "POST" | "PUT" | "PATCH" | "DELETE";
export const WRITE_METHODS: WriteMethod[] = ["POST", "PUT", "PATCH", "DELETE"];

/** 쓰기 짝 하나 (짝 파일 `writes[<동작>]`) */
export interface WriteMap {
  method: WriteMethod;
  path: string;
  path_params: Record<string, string>;
  body: Record<string, string>;
  ok: number[];
  result?: Record<string, string>;
  partial_codes?: string[];
}

/** 받은함 짝 (짝 파일 `inboxes.claims`, 읽기 짝과 같은 모양) */
export interface InboxMap {
  path: string;
  paging?: Record<string, unknown>;
  fields: Record<string, string>;
  values?: Record<string, Record<string, string>>;
}

export interface WriteActionState {
  action: WriteAction;
  title: string;
  service: string;
  money: boolean;
  write: WriteMap | null;
  enabled: boolean;
  /** "키에 쓰기 권한 없음"으로 적힌 시각 (없으면 null) */
  denied_at: string | null;
}

export interface SiteWritesState {
  configured: boolean;
  broken: boolean;
  actions: WriteActionState[];
  inboxes: { claims: InboxMap | null };
  count?: number | null;
}

export type WriteStatus = "sending" | "done" | "rejected" | "partial" | "unknown" | "handoff";

/** 최근 사이트 쓰기 한 줄 (본문 원문·키 없음) */
export interface RecentWrite {
  at: string | null;
  finished_at: string | null;
  service: string | null;
  action: string | null;
  status: WriteStatus | null;
  reason: string | null;
  reason_text: string | null;
  site_method: string | null;
  site_path: string | null;
  site_status: number | null;
  site_code: string | null;
  handoff_task_id: string | null;
  applied: "site" | "pending_human" | null;
}

export interface WritePath {
  method: string;
  path: string;
}

export const ACTION_LABEL: Record<WriteAction, string> = {
  inquiry_answer: "문의 답변",
  refund: "환불 (취소 결정)",
  coupon_issue: "쿠폰 발급",
  banner_post: "공지·배너 게시",
  banner_end: "공지·배너 내림",
  claim_result: "신청 처리 결과",
  payment_rollback: "결제 설정 되돌리기",
  fault_release: "결제 장애 해제",
};

/** 신청 받은함 칸 (dashboard-api `claims.to_claim`). 앞 셋은 꼭 있어야 한다 */
export const CLAIM_FIELDS: { name: string; label: string; required?: boolean }[] = [
  { name: "claim_id", label: "신청 번호", required: true },
  { name: "order_id", label: "주문(예약) 번호", required: true },
  { name: "status", label: "신청 상태", required: true },
  { name: "type", label: "신청 종류" },
  { name: "reason", label: "사유 종류" },
  { name: "description", label: "고객이 쓴 사유" },
  { name: "buyer_id", label: "구매자" },
  { name: "customer_name", label: "고객 이름" },
  { name: "created_at", label: "신청 시각" },
  { name: "site_refund_amount", label: "사이트 예상 환불액" },
  { name: "site_fee", label: "사이트 수수료" },
  { name: "site_fee_rate", label: "사이트 수수료율" },
];

/** 결과 칩 문구 (설계 "기록": applied=site "사이트에 반영됨", pending_human "사이트 반영 대기") */
export function appliedLabel(applied: string | null | undefined): string | null {
  if (applied === "site") return "사이트에 반영됨";
  if (applied === "pending_human") return "사이트 반영 대기";
  return null;
}

export type ChipTone = "ok" | "wait" | "bad" | "off";

/** 최근 사이트 쓰기 결과 칩: 장부 상태 → 문구·색 */
export function writeChip(row: Pick<RecentWrite, "status" | "reason" | "applied">): { label: string; tone: ChipTone } {
  switch (row.status) {
    case "done":
      return { label: "사이트에 반영됨", tone: "ok" };
    case "handoff":
      return { label: "사이트 반영 대기", tone: "wait" };
    case "partial":
      return { label: "반영됨 · 뒤처리 확인", tone: "wait" };
    case "unknown":
      return { label: "반영 여부 확인 필요", tone: "bad" };
    case "rejected":
      return row.reason === "no_write_permission"
        ? { label: "키 권한 없음 · 사이트 반영 대기", tone: "bad" }
        : { label: "사이트가 거절", tone: "bad" };
    case "sending":
      return { label: "보내는 중", tone: "off" };
    default:
      return { label: appliedLabel(row.applied) ?? "알 수 없음", tone: "off" };
  }
}

const PARAM = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** 경로의 `{이름}` 칸 (나온 순서, 겹치면 한 번) */
export function pathParams(path: string): string[] {
  const out: string[] = [];
  for (const found of path.matchAll(PARAM)) if (!out.includes(found[1])) out.push(found[1]);
  return out;
}

/** 경로를 바꾸면 경로 칸 짝을 맞춘다: 있던 칸 식은 두고, 새 칸은 빈 식, 없어진 칸은 뺀다 */
export function syncParams(path: string, params: Record<string, string>): Record<string, string> {
  return Object.fromEntries(pathParams(path).map((name) => [name, params[name] ?? ""]));
}

/** 쓰기 경로 후보에서 새 짝을 시작한다 */
export function startWrite(method: string, path: string): WriteMap {
  const m = (WRITE_METHODS as string[]).includes(method) ? (method as WriteMethod) : "POST";
  return { method: m, path, path_params: syncParams(path, {}), body: {}, ok: [200, 201] };
}

/** "200, 201" → [200, 201]. 숫자가 아닌 조각이 있으면 null */
export function parseCodes(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  if (!parts.length) return null;
  const codes = parts.map((p) => (/^\d{3}$/.test(p) ? Number(p) : NaN));
  return codes.some(Number.isNaN) ? null : codes;
}

/** "A, B" → ["A", "B"] */
export function parseList(text: string): string[] {
  return text.split(/[\s,]+/).filter(Boolean);
}

/** 저장 전 빠른 검사 (서버 `WriteMap` 검사와 같은 규칙만). 문제 문구 목록, 없으면 빈 목록 */
export function writeMapProblems(w: WriteMap): string[] {
  const out: string[] = [];
  const p = w.path;
  if (!p.startsWith("/") || p.startsWith("//") || /[@?#\\]/.test(p) || p.includes("://")) {
    out.push("경로는 /로 시작하는 사이트 안 경로만 쓸 수 있어요(@ ? # // 금지)");
  }
  const names = pathParams(p);
  const given = Object.keys(w.path_params);
  if (names.length !== given.length || names.some((n) => !given.includes(n))) {
    out.push("경로의 {이름} 칸과 경로 칸 짝이 맞지 않아요");
  }
  for (const [name, expr] of Object.entries(w.path_params)) if (!expr.trim()) out.push(`경로 칸 ${name}의 짝 식을 정해 주세요`);
  for (const [name, expr] of Object.entries(w.body)) {
    if (!name.trim()) out.push("본문 칸 이름이 비었어요");
    else if (!expr.trim()) out.push(`본문 칸 ${name}의 짝 식을 정해 주세요`);
  }
  if (!w.ok.length || w.ok.some((c) => c < 200 || c >= 300)) out.push("성공 코드는 2xx만 쓸 수 있어요");
  return out;
}

/** 신청 받은함 짝에서 아직 정하지 않은 꼭 있어야 하는 칸 이름 */
export function claimBlanks(m: InboxMap): string[] {
  return CLAIM_FIELDS.filter((f) => f.required && !(m.fields[f.name] ?? "").trim()).map((f) => f.label);
}

/** 빈 짝 식은 뺀 받은함 짝 (선택 칸을 비워 두면 짝 없음으로 저장) */
export function cleanInbox(m: InboxMap): InboxMap {
  const fields = Object.fromEntries(Object.entries(m.fields).filter(([, v]) => v.trim()));
  return { ...m, fields };
}
