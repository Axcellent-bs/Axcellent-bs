import { DEJAFIX_API_URL, DEJAFIX_WEB_URL } from "./env";

// 장애 대응 탭: DejaFix 화면을 띄우지 않고 DejaFix API에서 데이터만 받는다.
// 형식은 DejaFix/DejaFix contracts/api.md #10 `GET /api/incidents`, contracts/incident.schema.json 기준. 화면 없는 함수만 둔다.

export type Severity = { level: number; label: string };
export type AnalysisStatus = "pending" | "running" | "done" | "fallback" | "failed" | "skipped";

export interface Incident {
  id: number;
  source: string;
  zabbix: { eventid: string | null; triggerid: string | null; hostid: string | null };
  /** 호스트 보강 전에는 둘 다 null일 수 있다 (계약 #10) */
  host: { id: string | null; name: string | null };
  title: string;
  severity: Severity;
  status: "problem" | "resolved";
  started_at: string;
  resolved_at: string | null;
  tags: { tag: string; value: string }[];
  analysis: { status: AnalysisStatus | string; report_id: number | null; plan_id: number | null };
}

/** 목록의 meta. data_as_of·stale은 계약에 있지만 DejaFix 목 응답에는 없어서 선택으로 둔다 */
export interface IncidentMeta {
  next_cursor: string | null;
  data_as_of?: string | null;
  stale?: boolean;
}

/** 지금 진행 중인 장애만 (status=problem). DejaFix가 started_at 내림차순으로 준다 */
export async function currentIncidents(): Promise<{ data: Incident[]; meta: IncidentMeta }> {
  let res: Response;
  try {
    res = await fetch(`${DEJAFIX_API_URL}/api/incidents?status=problem&limit=100`, { cache: "no-store" });
  } catch {
    throw new Error("DejaFix 서버에 연결할 수 없습니다.");
  }
  if (res.status === 502) throw new Error("DejaFix 백엔드에 연결할 수 없습니다 (frontend/.env 의 DEJAFIX_PROXY_TARGET 확인).");
  if (res.status === 401) throw new Error("DejaFix 로그인 정보가 없거나 만료됐습니다 (frontend/.env 의 DEJAFIX_USERNAME·DEJAFIX_PASSWORD 확인).");
  if (!res.ok) throw new Error(`DejaFix가 오류를 돌려줬습니다 (HTTP ${res.status}).`);
  const body = (await res.json()) as { data?: Incident[]; meta?: IncidentMeta };
  return { data: body.data ?? [], meta: body.meta ?? { next_cursor: null } };
}

/** DejaFix 화면의 장애 상세 주소. 상세는 DejaFix가 그린다 */
export const incidentUrl = (id: number) => `${DEJAFIX_WEB_URL.replace(/\/$/, "")}/incidents/${id}`;

/** Zabbix 심각도 0~5 → 화면 표시 */
export const SEVERITY: Record<number, { label: string; tone: string }> = {
  5: { label: "재해", tone: "bg-red-600 text-white" },
  4: { label: "높음", tone: "bg-red-50 text-red-700" },
  3: { label: "보통", tone: "bg-amber-50 text-amber-700" },
  2: { label: "경고", tone: "bg-amber-50 text-amber-700" },
  1: { label: "정보", tone: "bg-ink-100 text-ink-700" },
  0: { label: "분류 안 됨", tone: "bg-ink-100 text-ink-700" },
};

/** analysis.status 변환 규칙(계약 #10)을 사람이 읽는 말로 */
export const ANALYSIS: Record<string, { label: string; tone: string }> = {
  pending: { label: "분석 대기", tone: "bg-ink-100 text-ink-700" },
  running: { label: "AI 분석 중", tone: "bg-brand-50 text-brand-700" },
  done: { label: "AI 분석 완료", tone: "bg-emerald-50 text-emerald-700" },
  fallback: { label: "규칙 기반 리포트", tone: "bg-amber-50 text-amber-700" },
  failed: { label: "분석 실패", tone: "bg-red-50 text-red-700" },
  skipped: { label: "분석 안 함", tone: "bg-ink-100 text-ink-500" },
};
