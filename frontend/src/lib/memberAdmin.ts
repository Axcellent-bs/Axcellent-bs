// 회원·서버 관리(/system/members) 회원 표의 서버 로그인 규칙 (dashboard-api `accounts.py`의 목록·update와 같은 규칙).
// 화면은 버튼을 끄고 이유를 보여 줄 뿐이고, 최종 판단은 서버가 다시 한다. 순수 계산이라 `npm test`가 그대로 돈다.
import { READER_AREA_IDS, readServerAccount, type ReaderAreaId, type ServerAccount, type ServerRole } from "./humanSession.ts";

/** 고객사 계정 역할 (회원 표에 나오는 역할) */
export const CUSTOMER_ROLES: readonly ServerRole[] = ["admin", "member"];

/** `GET /auth/accounts` 응답을 읽는다. 모양이 틀린 줄은 버리고 공개 칸만 남긴다 */
export function readAccountList(status: number, body: unknown): { ok: true; rows: ServerAccount[] } | { ok: false; message: string } {
  if (status >= 200 && status < 300) {
    const data = (body as { data?: unknown } | null)?.data;
    if (!Array.isArray(data)) return { ok: false, message: "계정 목록을 읽지 못했어요." };
    return { ok: true, rows: data.map(readServerAccount).filter((a): a is ServerAccount => !!a) };
  }
  const m = (body as { error?: { message?: unknown } } | null)?.error?.message;
  return { ok: false, message: typeof m === "string" && m ? m : `계정 목록을 읽지 못했어요 (${status})` };
}

/** 회원 표에 보일 고객사 계정만 (운영자 계정은 고객사 회원이 아니다) */
export const customerRows = (rows: readonly ServerAccount[]) => rows.filter((a) => CUSTOMER_ROLES.includes(a.role));

/** 확인 권한: 서버가 값을 주면 그 값, 아니면 역할로 (고객사 관리자는 있음) */
export const approverOf = (a: ServerAccount) => (typeof a.approver === "boolean" ? a.approver : a.role === "admin");

/** 비용 상한 변경 권한: 서버가 준 값(없으면 꺼짐) */
export const limitEditorOf = (a: ServerAccount) => a.limit_editor === true;

/** 정책 변경 권한: 서버가 준 값(없으면 꺼짐) */
export const policyEditorOf = (a: ServerAccount) => a.policy_editor === true;

/**
 * 정책 변경 권한을 줄 수 없는 이유. 줄 수 있으면 null.
 * 일반회원은 못 갖고, 주는 사람은 운영자이거나 정책 변경 권한이 있는 관리자다(서버가 다시 확인한다).
 */
export function policyEditorProblem(
  actor: { role: ServerRole; policy_editor?: boolean },
  target: Pick<ServerAccount, "role">,
): string | null {
  if (target.role === "member") return "일반회원은 정책 변경 권한을 가질 수 없어요. 먼저 관리자로 바꿔 주세요.";
  if (actor.role === "admin" && actor.policy_editor !== true) return "정책 변경 권한은 운영자나 정책 변경 권한이 있는 관리자만 줄 수 있어요.";
  if (actor.role !== "operator" && actor.role !== "admin") return "관리자만 바꿀 수 있어요.";
  return null;
}

/** 키 담당 권한·담당 고객사: 서버가 준 값(없으면 꺼짐·빈 목록) */
export const secretEditorOf = (a: ServerAccount) => a.secret_editor === true;
export const secretTenantsOf = (a: ServerAccount): string[] => a.secret_tenants ?? [];

/** 키 담당 권한을 줄 수 없는 이유. 일반회원은 못 갖고, 자기 자신에게는 못 준다(서버가 다시 확인한다) */
export function secretEditorProblem(actor: { id: string; role: ServerRole }, target: Pick<ServerAccount, "id" | "role">): string | null {
  if (target.role === "member") return "일반회원은 키 담당 권한을 가질 수 없어요. 먼저 관리자로 바꿔 주세요.";
  if (actor.role === "admin" && actor.id === target.id) return "자기 키 담당 권한은 스스로 바꿀 수 없어요. 운영자에게 요청해 주세요.";
  return null;
}

/** 비용 상한 변경 권한을 켤 수 없는 이유(일반회원은 못 켠다). 켤 수 있으면 null */
export function limitEditorProblem(target: Pick<ServerAccount, "role">): string | null {
  return target.role === "member" ? "일반회원은 비용 상한 변경 권한을 가질 수 없어요. 먼저 관리자로 바꿔 주세요." : null;
}

/**
 * 이 사람이 그 계정을 그 역할로 바꿀 수 있나. 바꿀 수 있으면 null, 아니면 이유.
 * 서버 규칙: 운영자는 모든 계정, 고객사 관리자는 고객사 계정만이고 운영자 역할은 못 준다.
 * 자기 역할은 스스로 못 바꾸고, 마지막 남은 고객사 관리자는 못 내린다.
 */
export function roleChangeProblem(
  actor: { id: string; role: ServerRole },
  target: ServerAccount,
  next: ServerRole,
  rows: readonly ServerAccount[],
): string | null {
  if (next === target.role) return null;
  if (actor.role !== "operator" && actor.role !== "admin") return "관리자만 역할을 바꿀 수 있어요.";
  if (actor.role === "admin" && (!CUSTOMER_ROLES.includes(target.role) || !CUSTOMER_ROLES.includes(next))) {
    return "고객사 관리자는 고객사 계정만 관리자·일반회원으로 바꿀 수 있어요.";
  }
  if (actor.id === target.id) return "자기 역할은 스스로 바꿀 수 없어요.";
  if (target.role === "admin" && rows.filter((r) => r.role === "admin").length <= 1) {
    return "마지막 남은 관리자는 내릴 수 없어요. 다른 관리자를 먼저 정해 주세요.";
  }
  return null;
}

/** 이 사람이 그 계정의 부서·확인 권한·열람 담당을 바꿀 수 있나 */
export function canEditProfile(actor: { role: ServerRole }, target: ServerAccount): boolean {
  if (actor.role === "operator") return true;
  return actor.role === "admin" && CUSTOMER_ROLES.includes(target.role);
}

export type AccountPatch = { role?: ServerRole; name?: string; dept?: string; buyer_id?: string; approver?: boolean; limit_editor?: boolean; policy_editor?: boolean; secret_editor?: boolean; secret_tenants?: string[]; readers?: ReaderAreaId[] };

/** `POST /auth/accounts/{id}/update` 본문: 보낼 칸만. 아무것도 없으면 null (빈 요청은 보내지 않는다) */
export function updateBody(patch: AccountPatch): AccountPatch | null {
  const out: AccountPatch = {};
  if (patch.role !== undefined) out.role = patch.role;
  if (patch.name !== undefined) out.name = patch.name.trim();
  if (patch.dept !== undefined) out.dept = patch.dept.trim();
  if (patch.buyer_id !== undefined) out.buyer_id = patch.buyer_id.trim();
  if (patch.approver !== undefined) out.approver = patch.approver;
  if (patch.limit_editor !== undefined) out.limit_editor = patch.limit_editor;
  if (patch.policy_editor !== undefined) out.policy_editor = patch.policy_editor;
  if (patch.secret_editor !== undefined) out.secret_editor = patch.secret_editor;
  if (patch.secret_tenants !== undefined) out.secret_tenants = [...new Set(patch.secret_tenants.map((t) => t.trim()).filter(Boolean))];
  if (patch.readers !== undefined) out.readers = READER_AREA_IDS.filter((r) => patch.readers?.includes(r));
  return Object.keys(out).length ? out : null;
}

/** 구매자 번호 입력 검사: 비우면 연결을 푼다. 아니면 buy_ 와 숫자 4자리(서버 규칙과 같다). 문제가 없으면 null */
export function buyerIdProblem(text: string): string | null {
  const t = text.trim();
  if (!t || /^buy_[0-9]{4}$/.test(t)) return null;
  return "구매자 번호는 buy_0001처럼 buy_와 숫자 4자리예요.";
}

/** 열람 담당 하나를 켜고 끈다 (순서는 READER_AREA_IDS 순) */
export function toggleReader(current: readonly ReaderAreaId[] | undefined, area: ReaderAreaId): ReaderAreaId[] {
  const on = new Set(current ?? []);
  if (on.has(area)) on.delete(area);
  else on.add(area);
  return READER_AREA_IDS.filter((r) => on.has(r));
}
