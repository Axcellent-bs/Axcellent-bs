// 트렌드 분석 의뢰 보고서 화면(components/data/TrendRequest.tsx · TrendReportView.tsx)의 순수 계산. node --test로 시험한다 (trendReport.test.ts).
// 숫자는 서버 값 그대로 글자로만 바꾼다. 모르면 0이 아니라 서버 이유를 보여 준다.
import type {
  BeatSlot, FieldSource, ReportStatus, SectionStatus, TrendReportBeat, TrendReportCaller, TrendReportGender, TrendReportInput,
} from "./data.ts";

/** 진행 중 보고서를 다시 읽는 간격 */
export const REPORT_POLL_MS = 5_000;

/** 대기·진행 중이면 계속 다시 읽는다. 완료·부분·실패·중단이면 멈춘다 */
export function keepPolling(status: ReportStatus): boolean {
  return status === "queued" || status === "running";
}

// ---- 주소: 보고서는 #request?id=<report_id>

export function reportHash(id: string): string {
  return `#request?id=${encodeURIComponent(id)}`;
}

/** 분석 의뢰 탭 주소에서 보고서 id. 다른 탭이거나 id가 없으면 null */
export function reportIdFromHash(hash: string): string | null {
  const m = /^#request\?(.*)$/.exec(hash);
  if (!m) return null;
  const id = new URLSearchParams(m[1]).get("id");
  return id ? id : null;
}

// ---- 상태 글자

export type ChipTone = "ok" | "warn" | "bad" | "info" | "muted";

export const REPORT_STATUS: Record<ReportStatus, { label: string; tone: ChipTone }> = {
  queued: { label: "대기", tone: "muted" },
  running: { label: "만드는 중", tone: "info" },
  done: { label: "완료", tone: "ok" },
  partial: { label: "일부 완료", tone: "warn" },
  failed: { label: "실패", tone: "bad" },
  interrupted: { label: "중단", tone: "bad" },
};

export const SECTION_STATUS: Record<SectionStatus, { label: string; tone: ChipTone }> = {
  waiting: { label: "대기", tone: "muted" },
  running: { label: "진행", tone: "info" },
  known: { label: "완료", tone: "ok" },
  unknown: { label: "자료 없음", tone: "warn" },
  failed: { label: "실패", tone: "bad" },
};

export const CALLER_LABEL: Record<TrendReportCaller, string> = { human: "사람", orchestrator: "총괄", planning: "기획" };

/** 의뢰 요약 칸 출처 (작은 글씨) */
export const SOURCE_LABEL: Record<FieldSource, string> = { input: "직접 입력", request_text: "의뢰문에서 추정", preset: "프리셋" };
export function sourceLabel(s: FieldSource | null | undefined): string {
  return s ? SOURCE_LABEL[s] ?? "출처 모름" : "출처 모름";
}

/** 섹션 근거 칩: 공식 API(초록) · AI 분석(파랑) · 사람 입력(노랑) */
export type EvidenceKind = "api" | "ai" | "human";
export const EVIDENCE: Record<EvidenceKind, { label: string; tone: ChipTone }> = {
  api: { label: "공식 API", tone: "ok" },
  ai: { label: "AI 분석", tone: "info" },
  human: { label: "사람 입력", tone: "warn" },
};
export type ReportPart = "search" | "hashtags" | "content" | "competitors" | "recommendation" | "summary";
export const SECTION_EVIDENCE: Record<ReportPart, EvidenceKind> = {
  search: "api", hashtags: "api", content: "api", competitors: "api", recommendation: "ai", summary: "ai",
};

// ---- 숫자

/** 변화 % 한 칸. null이면 서버 이유(없으면 "값 없음") */
export function pctText(v: number | null | undefined, reason: string | null | undefined): { text: string; known: boolean } {
  if (typeof v !== "number" || !Number.isFinite(v)) return { text: reason || "값 없음", known: false };
  const r = Math.round(v * 10) / 10;
  const body = `${Number.isInteger(r) ? r : r.toFixed(1)}%`;
  return { text: r > 0 ? `+${body}` : body, known: true };
}

/** 막대 길이 0~100: 같은 표의 가장 큰 절댓값 대비 */
export function barPct(v: number | null | undefined, maxAbs: number): number {
  if (typeof v !== "number" || !Number.isFinite(v) || maxAbs <= 0) return 0;
  return Math.min(100, (Math.abs(v) / maxAbs) * 100);
}

/** 0~1 → "62%" (모르면 null) */
export function shareText(v: number | null | undefined): string | null {
  return typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : null;
}

// ---- 콘티

export const BEAT_ORDER: BeatSlot[] = ["0-3", "3-15", "15-25", "25-30"];
export const BEAT_LABEL: Record<BeatSlot, string> = { "0-3": "0~3초 훅", "3-15": "3~15초 전환", "15-25": "15~25초 증거", "25-30": "25~30초 마무리" };

/** 콘티 칸을 시간 순서로 (모르는 slot은 뒤로, 원래 순서 유지) */
export function sortBeats(beats: TrendReportBeat[]): TrendReportBeat[] {
  const at = (b: TrendReportBeat) => { const i = BEAT_ORDER.indexOf(b.slot); return i < 0 ? BEAT_ORDER.length : i; };
  return beats.map((b, i) => ({ b, i })).sort((x, y) => at(x.b) - at(y.b) || x.i - y.i).map((x) => x.b);
}

// ---- 의뢰 요약·입력

export const GENDER_LABEL: Record<TrendReportGender, string> = { f: "여성", m: "남성" };
export const AGE_OPTIONS = ["10", "20", "30", "40", "50"] as const;

/** "여성 20·30대". 성별·연령을 모르면 "전체" */
export function targetText(gender: TrendReportGender | null, ages: string[]): string {
  const g = gender ? GENDER_LABEL[gender] : "전체";
  return ages.length ? `${g} ${ages.join("·")}대` : g;
}

/** "a, b,,a" → ["a","b"] (쉼표로 나눔, 앞의 @ 제거는 하지 않음, 빈 칸·중복 제거) */
export function splitList(text: string): string[] {
  return [...new Set(text.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean))];
}

export interface RequestForm {
  requestText: string;
  preset: string;
  product: string;
  /** "all" = 전체 */
  gender: TrendReportGender | "all";
  ages: string[];
  format: string;
  competitors: string;
  keywords: string;
}

export const EMPTY_FORM: RequestForm = { requestText: "", preset: "", product: "", gender: "all", ages: [], format: "", competitors: "", keywords: "" };

/** 폼 → 서버 입력. 의뢰문이 있으면 request_text만 보낸다. 없으면 직접 정한 칸(빈 칸은 null) */
export function buildInput(form: RequestForm, rebuild = false): TrendReportInput {
  const base = { caller: "human" as const, ...(rebuild ? { rebuild: true } : {}) };
  const text = form.requestText.trim();
  if (text) return { ...base, request_text: text };
  const list = (s: string) => { const v = splitList(s); return v.length ? v : null; };
  return {
    ...base,
    preset: form.preset || null,
    product: form.product.trim() || null,
    target_gender: form.gender === "all" ? null : form.gender,
    target_ages: form.ages.length ? [...form.ages].sort() : null,
    format: form.format.trim() || null,
    competitors: list(form.competitors),
    keywords: list(form.keywords),
  };
}

/** 직접 정한 칸이 하나라도 있는지 (의뢰문도 칸도 비면 만들기 버튼을 막는다) */
export function hasInput(form: RequestForm): boolean {
  const i = buildInput(form);
  return Boolean(i.request_text || i.preset || i.product || i.format || i.competitors || i.keywords || i.target_gender || i.target_ages);
}
