// 도구 연결 화면의 순수 함수: 새 연결 폼 상태(고르기 → 칸 → 연결 확인 → 저장)와 목록 모양.
// 화면(components/connect/ToolForm.tsx·DeployTools.tsx·DejafixConnect.tsx)이 쓰고, node --test로 시험한다 (toolForms.test.ts).
// 데이터를 지어내지 않는다: 상태·숫자는 서버가 준 값(check_ok·checked_at·check_detail)에서만 읽는다.
import type { ToolBody, ToolCheck, ToolConnection, ToolField, ToolKind } from "./toolConnections";

export const KIND_LABEL: Record<ToolKind, string> = {
  github_app: "GitHub 앱",
  github_actions: "GitHub Actions",
  jenkins: "Jenkins",
  dejafix: "DejaFix",
};
/** 배포 탭의 도구 고르기 (select) */
export const DEPLOY_CHOICES: readonly { id: ToolKind; label: string }[] = [
  { id: "github_actions", label: KIND_LABEL.github_actions },
  { id: "jenkins", label: KIND_LABEL.jenkins },
];
const DEPLOY = new Set<ToolKind>(["github_actions", "jenkins"]);

export type Busy = "test" | "save" | null;

export interface FormState {
  kind: ToolKind;
  /** 고치는 연결 (없으면 새 연결) */
  id?: string;
  config: Record<string, string>;
  secrets: Record<string, string>;
  /** 마지막 연결 확인 결과. 칸을 바꾸면 지운다 */
  checks: ToolCheck[] | null;
  passed: boolean | null;
  error: string | null;
  busy: Busy;
}

export type FormAction =
  | { type: "kind"; kind: ToolKind; fields: ToolField[] }
  | { type: "config"; key: string; value: string }
  | { type: "secret"; key: string; value: string }
  | { type: "busy"; busy: Busy }
  | { type: "tested"; ok: boolean; checks: ToolCheck[] }
  | { type: "failed"; message: string; checks?: ToolCheck[] }
  | { type: "saved" };

const blank = (fields: ToolField[], secret: boolean) =>
  Object.fromEntries(fields.filter((f) => f.secret === secret).map((f) => [f.key, ""]));

/** 새 연결이면 빈 칸, 고치는 연결이면 저장된 값(비밀 칸은 비움 → 비우면 그대로) */
export function initForm(kind: ToolKind, fields: ToolField[], existing?: ToolConnection): FormState {
  const config = blank(fields, false);
  if (existing) for (const k of Object.keys(config)) config[k] = existing.config[k] ?? "";
  return { kind, id: existing?.id, config, secrets: blank(fields, true), checks: null, passed: null, error: null, busy: null };
}

const edited = (s: FormState): FormState => ({ ...s, checks: null, passed: null, error: null });

export function formReducer(s: FormState, a: FormAction): FormState {
  switch (a.type) {
    case "kind":
      return a.kind === s.kind ? s : initForm(a.kind, a.fields);
    case "config":
      return edited({ ...s, config: { ...s.config, [a.key]: a.value } });
    case "secret":
      return edited({ ...s, secrets: { ...s.secrets, [a.key]: a.value } });
    case "busy":
      return { ...s, busy: a.busy, error: a.busy ? null : s.error };
    case "tested":
      return { ...s, busy: null, checks: a.checks, passed: a.ok, error: null };
    case "failed":
      return { ...s, busy: null, error: a.message, checks: a.checks ?? s.checks, passed: a.checks ? false : s.passed };
    case "saved":
      // 저장한 비밀 값은 다시 보여 주지 않는다. 새 연결 폼은 처음으로 돌린다
      return { ...s, id: undefined, config: Object.fromEntries(Object.keys(s.config).map((k) => [k, ""])), secrets: Object.fromEntries(Object.keys(s.secrets).map((k) => [k, ""])), checks: null, passed: null, error: null, busy: null };
  }
}

/** 연결 이름: 따로 묻지 않고 칸에서 만든다 (목록·서버 오류 문구에 쓰임) */
export function defaultName(kind: ToolKind, config: Record<string, string>): string {
  const c = (k: string) => (config[k] ?? "").trim();
  if (kind === "github_app") return `GitHub 앱 ${c("app_id")}`.trim();
  if (kind === "github_actions") return [c("repo"), c("workflow")].filter(Boolean).join(" · ") || KIND_LABEL.github_actions;
  if (kind === "jenkins") return `Jenkins ${c("job")}`.trim();
  return KIND_LABEL.dejafix;
}

/** 보낼 본문: 앞뒤 공백을 지우고, 빈 비밀 칸은 보내지 않는다(고칠 때 저장된 값 유지) */
export function bodyOf(s: FormState): ToolBody {
  const config = Object.fromEntries(Object.entries(s.config).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
  const secrets = Object.fromEntries(Object.entries(s.secrets).filter(([, v]) => v.trim()));
  return { kind: s.kind, name: defaultName(s.kind, config), config, secrets, ...(s.id ? { id: s.id } : {}) };
}

/** 비어 있는 필수 칸 이름. 고치는 연결의 비밀 칸은 저장돼 있으면 비워도 된다 */
export function missingFields(fields: ToolField[], s: FormState, existing?: ToolConnection): string[] {
  return fields
    .filter((f) => f.required)
    .filter((f) => (f.secret ? !s.secrets[f.key]?.trim() && !existing?.secrets_set[f.key] : !s.config[f.key]?.trim()))
    .map((f) => f.label);
}

/** 폼 아래 버튼: 관리자만 확인·저장 (일반 회원은 상태만 본다). 저장은 지금 칸으로 확인이 통과한 뒤에 */
export function formActions(admin: boolean, s: FormState): { id: "test" | "save"; disabled: boolean }[] {
  if (!admin) return [];
  return [
    { id: "test", disabled: s.busy !== null },
    { id: "save", disabled: s.busy !== null || s.passed !== true },
  ];
}

export type Tone = "ok" | "wait" | "bad" | "off";
/** 저장된 연결의 상태 chip */
export function statusOf(c: Pick<ToolConnection, "check_ok">): { tone: Tone; label: string } {
  return c.check_ok === true ? { tone: "ok", label: "연결됨" } : c.check_ok === false ? { tone: "bad", label: "확인 실패" } : { tone: "wait", label: "확인 전" };
}

export interface DeployRow {
  id: string;
  service: string;
  tool: string;
  target: string;
  checkedAt: string | null;
  status: { tone: Tone; label: string };
  detail: string | null;
}

/** 배포 탭 목록: 연결된 배포 도구만. 없으면 빈 목록 (빈 칸을 늘어놓지 않는다) */
export function deployRows(connections: ToolConnection[]): DeployRow[] {
  return connections
    .filter((c) => DEPLOY.has(c.kind))
    .map((c) => ({
      id: c.id,
      service: c.config.service ?? "",
      tool: KIND_LABEL[c.kind],
      target: c.kind === "github_actions" ? [c.config.repo, c.config.workflow].filter(Boolean).join(" · ") : c.config.job ?? "",
      checkedAt: c.checked_at,
      status: statusOf(c),
      detail: c.check_ok === false ? c.check_detail : null,
    }))
    .sort((a, b) => a.service.localeCompare(b.service, "ko"));
}

/** github_actions의 "GitHub 앱 연결" 고르기: 보관함의 github_app 연결 */
export const appChoices = (connections: ToolConnection[]) =>
  connections.filter((c) => c.kind === "github_app").map((c) => ({ id: c.id, label: c.name || `GitHub 앱 ${c.config.app_id ?? ""}` }));

/** "현재 장애 n건": 확인 결과 문구에서만 읽는다. 없으면 null (지어내지 않는다) */
export function incidentCount(detail: string | null | undefined): number | null {
  const m = detail?.match(/현재 장애 ([\d,]+)건/);
  return m ? Number(m[1].replace(/,/g, "")) : null;
}

export type DejafixView =
  | { state: "none" }
  | { state: "connected"; conn: ToolConnection; status: { tone: Tone; label: string }; incidents: number | null; checkedAt: string | null };

/** DejaFix 카드: 연결은 하나만 */
export function dejafixView(connections: ToolConnection[]): DejafixView {
  const conn = connections.find((c) => c.kind === "dejafix");
  if (!conn) return { state: "none" };
  return { state: "connected", conn, status: statusOf(conn), incidents: conn.check_ok ? incidentCount(conn.check_detail) : null, checkedAt: conn.checked_at };
}

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
