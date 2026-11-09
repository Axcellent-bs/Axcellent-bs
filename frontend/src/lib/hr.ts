import { ApiError } from "./apiError";
import { writeAuth } from "./api";
import { DASHBOARD_API_URL } from "./env";
import type { OrchIncident } from "./agentChat";

// 인사 AI 화면 → dashboard-api(/api/dashboard/...). 응답은 ax-backend 규약 {data, meta}.
// 모양은 ax-backend contracts/hr.py 모델 그대로다. 화면은 hr이 계산한 값을 다시 계산하지 않는다.

export interface HrCard {
  card_id: string;
  kind: string;
  title: string;
  body: string;
  status: string;
  confidence: "low" | "mid" | "high" | null;
  author: string;
  context_id: string;
  created_at: string;
  tags: string[];
  extra: Record<string, unknown>;
}
export interface ResolutionInput {
  outcome: "resolved" | "workaround" | "unresolved";
  root_cause?: string;
  action?: string;
  opened_at?: string | null;
  /** 사람이 적은 실제 해결 시각. 없으면 저장한 시각 */
  resolved_at?: string | null;
  prior_case_id?: string | null;
  draft_source?: "written_by_person" | "ai_draft_kept" | "ai_draft_edited";
}
export interface DuplicateMember { card_id: string; title: string; decision: string; cosine: number | null; verdict: "same" | "changed" | "separate" | null }
/** 사람이 병합할지 정하는 중복 결정 묶음. representative가 새 결정이다 */
export interface DuplicateGroup { group_id: string; area: string; representative: DuplicateMember; olders: DuplicateMember[]; suggested_title: string | null; created_at: string }
export interface MergedCard { card_id: string; title: string; merged_into: string; how: "auto" | "human"; cosine: number | null; at: string }
export interface DuplicateView { groups: DuplicateGroup[]; merged: MergedCard[] }
export interface AuditEntry { seq: number; at: string; kind: string; payload: Record<string, unknown> }
export interface PlanFinding {
  code: string; level: string; message: string; card_ids: string[];
  /** LLM이 쓴 설명(참고용). 판정에는 쓰이지 않고, 검증에 실패하면 없다 */
  explanation?: string | null;
}
export interface PlanCheck {
  verdict: "ok" | "warn" | "block" | string;
  findings: PlanFinding[];
  no_related_cards: boolean;
  similar_cards: { card_id: string; status: string; summary: string }[];
  computed: Record<string, unknown>;
}
export type OrgChangeKind = "transfer" | "join" | "leave";
export type OrgChangeStatus = "draft" | "confirmed" | "rejected";
export interface OrgChange {
  change_id: string; member_id: string; kind: OrgChangeKind;
  from_team: string | null; to_team: string | null;
  effective_date: string; counts_from_month: string; status: OrgChangeStatus;
  note: string | null; basis_card_id: string | null; basis_title: string | null;
  created_at: string; decided_at: string | null;
}
/** 발령 기록과 명부 등록 상태. roster_complete가 참이어야 재무에 나가는 인원이 확정 기록으로 센 값이 된다 */
export interface OrgChanges { changes: OrgChange[]; registered_members: number; expected_members: number; roster_complete: boolean; collected_at: string }
export interface OrgChangeInput { member_id: string; kind: OrgChangeKind; from_team?: string; to_team?: string; effective_date: string; note?: string; basis_card_id?: string }
/** 발령 사유와 비슷한 과거 업무 결정 카드. 찾기만 하고, 근거로 연결하는 것은 사람이 고를 때다 */
export interface OrgBasisCandidate { card_id: string; title: string; summary: string; similarity: number; rejected: boolean }
export interface OrgBasis { candidates: OrgBasisCandidate[]; method: "dense" | "dice"; collected_at: string }
/** 발령 문장을 칸으로 나눈 제안. 저장은 하지 않는다. available이 false면 직접 입력한다 */
export interface OrgSuggestion { kind: OrgChangeKind | null; member_id: string | null; from_team: string | null; to_team: string | null; effective_date: string | null; from_team_from_roster: boolean }
export interface OrgParse { available: boolean; suggestion: OrgSuggestion | null; warnings: string[]; message: string | null }
/** 승인하는 사람이 읽을 설명문. 숫자는 코드 값이고 fallback이면 템플릿 문장이다 */
export interface OrgDescription { change_id: string; month: string; deltas: { team: string; delta: number }[]; text: string; fallback: boolean; llm_used: boolean }
export interface OrgHeadcount {
  available: boolean; month: string; message?: string;
  rows?: { team: string; headcount: number }[]; basis?: string | null; example?: boolean;
}
export interface PlanDraft { title: string; summary: string; area: string; kind: "spec" | "ticket" | "deploy_plan" }

// 결정 카드 본문에 들어 있는 단계 코드·상태 코드를 사람이 읽는 말로 바꿔 보여 준다(저장된 값은 그대로 둔다).
const STEP_NAME: Record<string, string> = {
  compensation: "영향 고객 일괄 보상", recovery: "복구 검증", late_compensation: "복구 뒤 놓친 고객 보상 대기",
  recovered_draft: "정상화 공지 초안", cost: "처리 결과 장부에 기록",
  diagnose: "원인 진단", impact: "영향 분석", similar_cases: "비슷한 과거 이벤트 찾기", root_cause: "원인 후보 추정",
  cause_check: "원인 확인 요청", rollback: "문제 배포를 이전 버전으로 되돌리기", related_prs: "관련 PR 찾기",
  link_incident: "이벤트에 PR 연결", notice: "점검 공지 초안", notice_publish: "점검 공지 게시", publish_notice: "점검 공지 게시",
  recovered_notice: "정상화 공지", kpi: "처리 시간 요약", record: "이벤트 기록", resolution: "해결 내용 기록",
};
const REASON_CODE: Record<string, string> = {
  gate_rejected: "안전 확인 단계에서 막힘", metric_not_supported: "아직 분석하지 못하는 항목", service_disabled: "해당 서비스가 꺼져 있음",
  human_input: "사람 확인이 필요함", cause_confident: "원인이 충분히 뚜렷함", undetermined: "원인을 특정하지 못함",
};
const DEPT_NAME: Record<string, string> = {
  infra: "인프라 AI", data: "데이터분석 AI", planning: "기획 AI", dev: "개발 AI", marketing: "마케팅 AI",
  support: "고객지원 AI", finance: "재무 AI", hr: "인사 AI", orchestrator: "총괄 AI",
};
// 글 속 영어 지표·구성요소 이름과 문의 처리 결과 코드를 한국어로 (긴 이름부터 바꾼다)
const TERM_KO: Record<string, string> = {
  payment_fail_rate: "결제 실패율", error_rate_5xx: "서버 오류율", p95_latency_ms: "응답 시간", order_fail_rate: "주문 실패율",
  "shop-api": "쇼핑몰 서버", payment: "결제", shipping: "배송", search: "검색", api: "판매 사이트", db: "주문 DB",
  confirm_spec: "기획서 확정", answered: "답변 완료", claim_decided: "클레임 결정", compensated: "보상 완료", escalated: "상담원에게 넘김",
};
const TERM_RE = new RegExp(`(?<![A-Za-z0-9_-])(${Object.keys(TERM_KO).sort((a, b) => b.length - a.length).join("|")})(?![A-Za-z0-9_-])`, "g");
/** 직원 번호 emp_01을 e1로 보여 준다(저장된 값은 그대로) */
export const showEmp = (text: string) => text.replace(/(?<![A-Za-z0-9_])emp_0*(\d+)(?![A-Za-z0-9_])/g, "e$1");
/** 입력한 직원 번호 e1을 저장 형식 emp_01로 (emp_01은 그대로) */
export const storedEmp = (id: string) => id.replace(/^e0*(\d+)$/i, (_, n: string) => `emp_${n.padStart(2, "0")}`);
/** 문장 속 e3 같은 직원 번호를 저장 형식으로 */
export const storedEmpInText = (text: string) => text.replace(/(?<![A-Za-z0-9_])e0*(\d+)(?![A-Za-z0-9_])/gi, (_, n: string) => `emp_${n.padStart(2, "0")}`);
/** 화면에 보일 글: 코드·영어 이름을 쉬운 말로 */
export const plainText = (text: string) => readableBody(text).replace(TERM_RE, (_, k: string) => TERM_KO[k] ?? k);

export interface CardRow { label: string; text: string; refs?: string[] }
const isMachineRef = (t: string) => /^(step:|seed:|ctx_)/.test(t.trim());
function firstSentence(text: string, max = 90): string {
  const clean = text.replace(/^(교훈:\s*)+/, "").trim();
  const m = /^(.*?(?:다|요|음)\.)(?:\s|$)/.exec(clean);
  const s = m ? m[1] : clean;
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}
/** 카드 본문에서 읽는 사람에게 꼭 필요한 줄만 뽑는다. 저장된 본문은 그대로 두고 화면에서만 줄인다 */
export function cardRows(card: Pick<HrCard, "title" | "body" | "kind">): CardRow[] {
  const [main, resolution] = card.body.split(/\r?\n\[해결 기록\]/);
  const fields: Record<string, string> = {};
  let last = "";
  for (const line of main.split(/\r?\n/)) {
    const m = /^(결정|이유|검토한 대안|근거|교훈): ?(.*)$/.exec(line);
    if (m) { last = m[1]; fields[last] = m[2]; } else if (last) fields[last] += ` ${line}`;
  }
  const rows: CardRow[] = [];
  const titleText = plainText(card.title).replace(/^\[[^\]]*\]\s*/, "");
  const decision = plainText(fields["결정"] ?? "").replace(/ · 문의 #\d+ 처리 결과 기록: /, " · ");
  if (decision && !titleText.startsWith(decision.slice(0, 20)) && !decision.startsWith(titleText.slice(0, 20))) rows.push({ label: "결정", text: decision });
  let reason = plainText(fields["이유"] ?? "");
  const unfinished = /끝내지 못한 일: (.+)$/.exec(reason);
  if (unfinished) reason = unfinished[1] === "없음" ? "모든 단계를 끝냈어요" : `끝내지 못한 일: ${unfinished[1]}`;
  else if (/끝낸 일:/.test(reason)) reason = "";
  if (reason) rows.push({ label: "이유", text: reason });
  const alt = plainText(fields["검토한 대안"] ?? "");
  if (card.kind !== "incident" && alt && alt !== "없음") rows.push({ label: "검토한 대안", text: alt });
  const refs = fields["근거"] ?? "";
  if (card.kind !== "incident" && refs && refs !== "확인할 수 없음" && !refs.split(",").every(isMachineRef)) rows.push({ label: "근거", text: plainText(refs), refs: refs.split(",").map((t) => t.trim()).filter(Boolean) });
  if (fields["교훈"]) rows.push({ label: "교훈", text: firstSentence(plainText(fields["교훈"])) });
  if (resolution) {
    const get = (k: string) => new RegExp(`${k}: (.+)`).exec(resolution)?.[1]?.trim();
    const known = (v?: string) => (v && v !== "확인할 수 없음" ? plainText(v) : null);
    const parts = [get("결과"), known(get("원인")) && `원인 ${known(get("원인"))}`, known(get("조치")) && `조치 ${known(get("조치"))}`].filter(Boolean);
    if (parts.length) rows.push({ label: "해결", text: parts.join(" · ") });
  }
  return rows;
}

/** 카드를 남긴 주체 이름을 사람이 읽는 말로 (총괄·부서 코드) */
export const authorKo = (author: string) => DEPT_NAME[author] ?? author;

export function readableBody(body: string): string {
  return body
    .replace(/^근거: (.+)$/m, (_, l: string) => `근거: ${l.split(", ").map((t) => STEP_NAME[t.replace(/^step:/, "")] ?? t).join(", ")}`)
    .replace(/미완료\(([a-z_]+)\)/g, (_, k: string) => `미완료: ${STEP_NAME[k] ?? k}`)
    .replace(/step:([a-z_]+)/g, (_, k: string) => STEP_NAME[k] ?? k)
    .replace(/ (?:처리|실행) 결과(?:\.| —) (?:끝낸 일|완료):/g, "에서 끝낸 일:")
    .replace(/; 미완료:/g, ". 끝내지 못한 일:")
    .replace(/: (\w+)로 이관 \(([a-z_]+)\)/g, (_, d: string) => `: ${DEPT_NAME[d] ?? d}에게 넘김`)
    .replace(/\(([a-z_:]+(?: [a-z_]+)?)\)/g, (m, k: string) => REASON_CODE[k.replace(/^failed: /, "")] ? `(${REASON_CODE[k.replace(/^failed: /, "")]})` : STEP_NAME[k] ? `(${STEP_NAME[k]})` : m)
    .replace(/원인 배포 롤백/g, "문제 배포를 이전 버전으로 되돌리기")
    .replace(/사후 감사( 대상)?/g, "나중에 사람이 점검$1")
    .replace(/롤백/g, "이전 버전으로 되돌리기")
    .replace(/되돌리기([과이은을])/g, (_, j: string) => `되돌리기${({ 과: "와", 이: "가", 은: "는", 을: "를" } as Record<string, string>)[j]}`);
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}/api/dashboard${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...writeAuth(init?.method, path), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  let body: { data?: T; error?: { message?: string } } | null = null;
  try { body = await res.json(); } catch { /* 본문이 JSON이 아님 */ }
  if (!res.ok) throw new ApiError(body?.error?.message ?? `요청 실패 (${res.status})`, res.status);
  return (body?.data ?? null) as T;
}
const post = <T>(path: string, body: unknown) => call<T>(path, { method: "POST", body: JSON.stringify(body) });

export const hr = {
  candidateCards: () => call<HrCard[]>("/candidate-cards"),
  /** 확정된(후보가 아닌) 결정 카드, 최근 순. 이벤트 번호를 주면 그 이벤트에서 만든 카드만 */
  decisionCards: (contextId?: string, limit = 50) =>
    call<HrCard[]>(`/decision-cards?limit=${limit}${contextId ? `&context_id=${encodeURIComponent(contextId)}` : ""}`),
  decide: (cardId: string, decision: "approve" | "reject") =>
    post<{ card_id: string; status: string }>(`/candidate-cards/${encodeURIComponent(cardId)}/decision`, { decision }),
  /** 사람이 이벤트의 해결 내용을 적는다. 소요 시간은 서버가 openedAt부터 잰다 */
  recordResolution: (contextId: string, body: ResolutionInput) =>
    post<{ outcome: string; card_id: string | null }>(`/incidents/${encodeURIComponent(contextId)}/resolution`, body),
  duplicateGroups: () => call<DuplicateView>("/duplicate-groups"),
  /** 중복 묶음을 병합(title은 사람이 확정한 대표 제목)하거나 그대로 둔다 */
  decideDuplicate: (g: DuplicateGroup, decision: "merge" | "dismiss", title?: string) =>
    post<{ outcome: string; merged_ids: string[] }>(`/duplicate-groups/${encodeURIComponent(g.group_id)}/decision`, {
      representative_id: g.representative.card_id,
      decision,
      ...(title?.trim() ? { title: title.trim() } : {}),
    }),
  restoreMerge: (cardId: string) =>
    post<{ outcome: string; card_id: string | null }>(`/cards/${encodeURIComponent(cardId)}/restore-merge`, {}),
  orgChanges: () => call<OrgChanges>("/org-changes"),
  /** 발령 초안 저장. 승인 없이 저장되고, 확정하기 전에는 팀 인원에 세지 않는다 */
  recordOrgChange: (body: OrgChangeInput) => post<{ change: OrgChange }>("/org-changes", body),
  decideOrgChange: (changeId: string, decision: "approve" | "reject") =>
    post<{ change: OrgChange }>(`/org-changes/${encodeURIComponent(changeId)}/decision`, { decision }),
  setRosterComplete: (complete: boolean) =>
    post<{ roster_complete: boolean; registered_members: number; expected_members: number }>("/org-roster/complete", { complete }),
  orgBasis: (reason: string) => post<OrgBasis>("/org-changes/basis", { reason }),
  orgParse: (text: string) => post<OrgParse>("/org-changes/parse", { text }),
  orgDescription: (changeId: string) => call<OrgDescription>(`/org-changes/${encodeURIComponent(changeId)}/description`),
  /** 그 달 팀별 인원 미리 보기. 재무에 나가는 값과 같다 */
  orgHeadcount: (month: string) => call<OrgHeadcount>(`/org-headcount?month=${encodeURIComponent(month)}`),
  activity: (contextId: string) => call<AuditEntry[]>(`/hr-activity/${encodeURIComponent(contextId)}`),
  checkPlan: (draft: PlanDraft) => post<PlanCheck>("/plan-checks", { draft }),
  /** 총괄 이벤트 목록(dashboard-api가 총괄 /api/incidents를 중계). 인사 AI의 이벤트 기록 단계(record_case)가 들어 있다 */
  incidents: () => call<OrchIncident[]>("/incidents"),
};
