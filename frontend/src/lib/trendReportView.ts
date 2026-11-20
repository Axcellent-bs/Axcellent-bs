// 트렌드 분석 의뢰 보고서 화면(components/data/TrendRequest.tsx · TrendReportView.tsx)의 순수 계산. node --test로 시험한다 (trendReport.test.ts).
// 숫자는 서버 값 그대로 글자로만 바꾼다. 모르면 0이 아니라 서버 이유를 보여 준다.
import type {
  BeatSlot, FieldSource, ReportStatus, SectionStatus, ThemeMode, TrendEvidenceItem, TrendReport, TrendReportBeat, TrendReportBrief, TrendReportCaller,
  TrendReportCompetitorRow, TrendReportGender, TrendReportHashtagRow, TrendReportInput, TrendReportSearchRow, TrendThemeRow, TrendThemeSeenIn,
  TrendThemeSection, TrendTraitStructured,
} from "./data.ts";

/** 진행 중 보고서를 다시 읽는 간격 */
export const REPORT_POLL_MS = 5_000;

/** 대기·진행 중이면 계속 다시 읽는다. 완료·부분·실패·중단이면 멈춘다 */
export function keepPolling(status: ReportStatus): boolean {
  return status === "queued" || status === "running";
}

// ---- 보고서 번호 표기 (공통 계약): "TR-" + report_id에서 "tr_" 뒤 앞 4글자 대문자 (tr_b8358e… → TR-B835)
// 보고서 화면·지난 의뢰 목록·대화 카드가 모두 이 함수 하나를 쓴다. 호출·링크에는 원래 id를 쓴다

export function reportLabel(id: string): string {
  return `TR-${id.replace(/^tr_/, "").slice(0, 4).toUpperCase()}`;
}

/** 인쇄(PDF) 때만 보이는 머리글 한 줄: "TR-xxxx · 회사 이름 · 만든 시각(한국) · 상태". 회사·시각을 모르면 그 칸은 뺀다 */
export function printHeaderLine(r: { report_id: string; created_at: string | null; status: ReportStatus }, company?: string | null): string {
  const t = r.created_at ? Date.parse(r.created_at) : NaN;
  let at: string | null = null;
  if (Number.isFinite(t)) {
    const d = new Date(t + 9 * 3600_000);
    const p = (n: number) => String(n).padStart(2, "0");
    at = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  }
  return [reportLabel(r.report_id), company?.trim() || null, at, REPORT_STATUS[r.status]?.label ?? r.status].filter(Boolean).join(" · ");
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
  skipped: { label: "해당 없음", tone: "muted" },
  empty: { label: "확인함 · 없음", tone: "muted" },
};

export const CALLER_LABEL: Record<TrendReportCaller, string> = { human: "사람", orchestrator: "총괄", planning: "기획" };

/** 의뢰 요약 칸 출처 (작은 글씨) */
export const SOURCE_LABEL: Record<FieldSource, string> = {
  input: "직접 입력", request_text: "의뢰문에서 추정", preset: "프리셋", company: "회사 정보로 채움", theme: "주제 후보에서",
};
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
export type ReportPart = "theme" | "search" | "hashtags" | "content" | "competitors" | "recommendation" | "summary";
export const SECTION_EVIDENCE: Record<ReportPart, EvidenceKind> = {
  theme: "api", search: "api", hashtags: "api", content: "api", competitors: "api", recommendation: "ai", summary: "ai",
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
  goal: string;
  /** 주제(예: 아이돌, #1540). 비면 theme·theme_mode 둘 다 null */
  theme: string;
  /** 체크 = 이 주제 안에서 후보를 찾아 순위 매기기(find), 안 하면 use */
  themeFind: boolean;
}

export const EMPTY_FORM: RequestForm = {
  requestText: "", preset: "", product: "", gender: "all", ages: [], format: "", competitors: "", keywords: "", goal: "", theme: "", themeFind: false,
};

/** 폼 주제 칸 → 서버 값. 주제가 비면 둘 다 null */
export function themeInput(theme: string, find: boolean): { theme: string | null; theme_mode: ThemeMode | null } {
  const t = theme.trim();
  return t ? { theme: t, theme_mode: find ? "find" : "use" } : { theme: null, theme_mode: null };
}

/** 보고서의 의뢰 요약 → "직접 정하기" 폼 (고쳐서 다시 만들기). 의뢰문은 비운다 */
export function formFromBrief(b: TrendReportBrief): RequestForm {
  return {
    ...EMPTY_FORM,
    preset: b.preset ?? "",
    product: b.product ?? "",
    goal: b.goal ?? "",
    gender: b.target_gender ?? "all",
    ages: [...b.target_ages],
    format: b.format ?? "",
    competitors: b.competitors.join(", "),
    keywords: b.keywords.join(", "),
    theme: b.theme ?? "",
    themeFind: b.theme_mode === "find",
  };
}

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
    ...(form.goal.trim() ? { goal: form.goal.trim() } : {}),
    target_gender: form.gender === "all" ? null : form.gender,
    target_ages: form.ages.length ? [...form.ages].sort() : null,
    format: form.format.trim() || null,
    competitors: list(form.competitors),
    keywords: list(form.keywords),
    // 주제가 있을 때만 (비면 칸을 넣지 않는다 = 서버 기본 null)
    ...(themeInput(form.theme, form.themeFind).theme ? themeInput(form.theme, form.themeFind) : {}),
  };
}

/** 직접 정한 칸이 하나라도 있는지 (의뢰문도 칸도 비면 만들기 버튼을 막는다) */
export function hasInput(form: RequestForm): boolean {
  const i = buildInput(form);
  return Boolean(i.request_text || i.preset || i.product || i.format || i.competitors || i.keywords || i.target_gender || i.target_ages || i.theme);
}

// ---- v2 헤더: "항목 | 칩"

/** star: 주제 후보에서 온 키워드(앞에 작은 ★) */
export interface HeaderChip { text: string; sub: string | null; star?: boolean }
export interface HeaderRow { key: "product" | "theme" | "target" | "format" | "competitors" | "keywords"; label: string; chips: HeaderChip[]; sourceTag: string | null }

/** 출처 꼬리표: 직접 입력(또는 모름)이면 없음 */
export function sourceTag(s: FieldSource | null | undefined): string | null {
  if (s === "request_text") return "의뢰문에서 추정";
  if (s === "preset") return "프리셋에서 채움";
  if (s === "company") return "회사 정보로 채움";
  if (s === "theme") return "주제 후보에서";
  return null;
}

export const THEME_MODE_LABEL: Record<ThemeMode, string> = { find: "찾아서 순위", use: "그대로 씀" };

/** 경쟁사 칩: 이름이 있으면 이름 + "@아이디", 없으면 "@아이디"만 */
export function competitorChip(id: string, names?: Record<string, string | null> | null): HeaderChip {
  const name = names?.[id]?.trim();
  return name ? { text: name, sub: `@${id}` } : { text: `@${id}`, sub: null };
}

/** "여성" / "20대" / "30대" 칩 (모르면 "전체") */
export function targetChips(gender: TrendReportGender | null, ages: string[]): string[] {
  const out: string[] = [];
  if (gender) out.push(GENDER_LABEL[gender]);
  for (const a of ages) out.push(`${a}대`);
  return out.length ? out : ["전체"];
}

/** 헤더 행들. 제품 행의 목표 칩은 화면이 brief.goal로 그린다. 결과물은 format_parts가 있으면 그것, 없으면 통째로 한 칩 (공백으로 쪼개지 않는다) */
export function headerRows(b: TrendReportBrief, theme?: TrendThemeSection | null): HeaderRow[] {
  const parts = (b.format_parts ?? []).filter(Boolean);
  const format: HeaderChip[] = parts.length ? parts.map((p) => ({ text: p, sub: null })) : b.format ? [{ text: b.format, sub: null }] : [];
  const src = b.sources;
  // 주제 후보에서 온 키워드: 키워드 칸 출처가 theme이면 후보 표에 있는 이름에 ★
  const fromTheme = new Set(src.keywords === "theme" ? (theme?.rows ?? []).map((r) => r.name) : []);
  const rows: HeaderRow[] = [
    { key: "product", label: "제품", chips: b.product ? [{ text: b.product, sub: null }] : [], sourceTag: sourceTag(src.product) },
    { key: "target", label: "누구에게", chips: targetChips(b.target_gender, b.target_ages).map((t) => ({ text: t, sub: null })), sourceTag: sourceTag(src.target) },
    { key: "format", label: "무엇을", chips: format, sourceTag: sourceTag(src.format) },
    { key: "competitors", label: `경쟁사 ${b.competitors.length}`, chips: b.competitors.map((c) => competitorChip(c, b.competitor_names)), sourceTag: sourceTag(src.competitors) },
    { key: "keywords", label: `키워드 ${b.keywords.length}`, chips: b.keywords.map((k) => ({ text: k, sub: null, star: fromTheme.has(k) })), sourceTag: sourceTag(src.keywords) },
  ];
  if (b.theme) {
    const chips: HeaderChip[] = [{ text: b.theme, sub: null }];
    if (b.theme_mode) chips.push({ text: THEME_MODE_LABEL[b.theme_mode], sub: null });
    rows.splice(1, 0, { key: "theme", label: "주제", chips, sourceTag: sourceTag(src.theme) });
  }
  return rows;
}

// ---- #1540 0 주제 후보

/** 주제 후보 섹션을 보일지: 섹션이 있고 skipped가 아니며 brief가 find일 때만 (예전 저장본·use·없음은 숨김) */
export function showThemeSection(r: Pick<TrendReport, "sections"> & { brief?: Pick<TrendReportBrief, "theme" | "theme_mode"> }): boolean {
  const t = r.sections.theme;
  return Boolean(t && t.status !== "skipped" && r.brief?.theme_mode === "find" && r.brief.theme);
}

/** 진행을 세는 섹션 이름 (주제 후보는 보일 때만 더한다). brief가 없으면 주제 후보는 세지 않는다 */
export function countedSections(r: Pick<TrendReport, "sections"> & { brief?: Pick<TrendReportBrief, "theme" | "theme_mode"> }): ("theme" | "search" | "hashtags" | "content" | "competitors" | "recommendation")[] {
  const base = ["search", "hashtags", "content", "competitors", "recommendation"] as const;
  return showThemeSection(r) ? ["theme", ...base] : [...base];
}

export const SECTION_TITLE: Record<ReturnType<typeof countedSections>[number], string> = {
  theme: "주제 후보", search: "검색", hashtags: "해시태그", content: "콘텐츠", competitors: "경쟁사", recommendation: "추천",
};

export interface SectionNote { key: ReturnType<typeof countedSections>[number]; title: string; reason: string }

/** 끝난 보고서의 칸 나누기: missing = 확인 못 한 칸(unknown·failed, 일부 완료 이유),
 *  empty = 확인했지만 없던 칸(완료로 셈, 접어서 따로). 이유가 없으면 "이유를 받지 못했어요" */
export function sectionNotes(r: Pick<TrendReport, "sections"> & { brief?: Pick<TrendReportBrief, "theme" | "theme_mode"> }): { missing: SectionNote[]; empty: SectionNote[] } {
  const missing: SectionNote[] = [];
  const empty: SectionNote[] = [];
  for (const key of countedSections(r)) {
    const s = r.sections[key];
    if (!s) continue;
    const note = { key, title: SECTION_TITLE[key], reason: s.reason ?? "이유를 받지 못했어요" };
    if (s.status === "empty") empty.push(note);
    else if (s.status === "unknown" || s.status === "failed") missing.push(note);
  }
  return { missing, empty };
}

export const SEEN_IN_LABEL: Record<TrendThemeSeenIn, string> = { radar: "인스타 레이더", youtube: "YouTube 인기", llm: "AI 제안" };

export interface ThemeRowView { name: string; level: number; change: string; seenIn: string[]; products: string }

/** 표 한 줄: 변화가 없으면 "–", 상품이 없으면 "–" */
export function themeRowView(r: TrendThemeRow): ThemeRowView {
  const p = pctText(r.change_pct, null);
  return {
    name: r.name,
    level: Math.max(0, Math.min(100, Math.round(r.level))),
    change: p.known ? p.text : "–",
    seenIn: r.seen_in.map((s) => SEEN_IN_LABEL[s] ?? s),
    products: r.products.length ? r.products.join(", ") : "–",
  };
}

// ---- v2 AI 추천: 콘티 타임라인 · 근거

export const SLOT_SECONDS: Record<BeatSlot, number> = { "0-3": 3, "3-15": 12, "15-25": 10, "25-30": 5 };

/** 칸 폭(%): 초에 비례 (3:12:10:5). 합이 100 */
export function beatWidths(slots: string[]): number[] {
  const secs = slots.map((s) => SLOT_SECONDS[s as BeatSlot] ?? 5);
  const total = secs.reduce((a, b) => a + b, 0);
  return total > 0 ? secs.map((s) => (s / total) * 100) : secs;
}

/** 근거 id("search:1") → 근거 데이터 행의 DOM id */
export function evidenceDomId(id: string): string {
  return `tr-ev-${id.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** 칸의 근거 항목: evidence_items가 있으면 그것, 옛 저장본이면 문장만 (id 없음) */
export function beatEvidence(b: TrendReportBeat): { id: string | null; text: string }[] {
  if (b.evidence_items?.length) return b.evidence_items.map((e) => ({ id: e.id, text: e.text }));
  return b.evidence.map((text) => ({ id: null, text }));
}

/** 핵심 근거 칩: beat 순서대로 id 중복 없이 최대 3개의 short */
export function keyEvidence(beats: TrendReportBeat[], max = 3): TrendEvidenceItem[] {
  const seen = new Set<string>();
  const out: TrendEvidenceItem[] = [];
  for (const b of beats) {
    for (const e of b.evidence_items ?? []) {
      if (seen.has(e.id) || !e.short) continue;
      seen.add(e.id);
      out.push(e);
      if (out.length >= max) return out;
    }
  }
  return out;
}

/** 근거 목록을 펼쳐 둘지: 인쇄 중이면 항상 펼친다 */
export function evidenceExpanded(open: boolean, printing: boolean): boolean {
  return open || printing;
}
/** 화면에서는 접혀도 DOM에 두고, 인쇄에서는 보이게 하는 클래스 */
export function evidenceListClass(open: boolean): string {
  return open ? "block" : "hidden print:block";
}

// ---- v2 1 검색

export type SearchKind = "filtered" | "small_base" | "all" | "volume_only" | "none";

/** basis 없는 옛 저장본: 검색 값이 있으면 filtered, 없으면 none */
export function searchKind(r: TrendReportSearchRow): SearchKind {
  const b = r.basis ?? (r.search_change_pct != null ? "filtered" : "none");
  if (b === "filtered" && r.small_base) return "small_base";
  return b;
}

const KIND_ORDER: Record<SearchKind, number> = { filtered: 0, small_base: 1, all: 2, volume_only: 3, none: 4 };

/** 거른 값 변화 큰 순 → 기준값 작음 → 전체 기준 → 검색량만 → 없음 (같으면 원래 순서) */
export function sortSearchRows(rows: TrendReportSearchRow[]): TrendReportSearchRow[] {
  return rows.map((r, i) => ({ r, i, k: searchKind(r) }))
    .sort((x, y) => KIND_ORDER[x.k] - KIND_ORDER[y.k]
      || (x.k === "filtered" ? (y.r.search_change_pct ?? 0) - (x.r.search_change_pct ?? 0) : 0) || x.i - y.i)
    .map((x) => x.r);
}

export interface SearchMain { kind: SearchKind; text: string; known: boolean; /** 막대에 쓸 값 */ bar: number | null; small: string | null }

/** 월 검색 글자: note가 under_10이면 "월 검색 10 미만"(숫자 0으로 보이지 않게), 숫자가 있으면 "월 검색 N", 둘 다 없으면 null */
export function volumeText(r: Pick<TrendReportSearchRow, "monthly_volume" | "monthly_volume_note">): string | null {
  if (r.monthly_volume_note === "under_10") return "월 검색 10 미만";
  return r.monthly_volume != null ? `월 검색 ${r.monthly_volume.toLocaleString("ko-KR")}` : null;
}

/** 검색 한 줄의 오른쪽 값 */
export function searchMain(r: TrendReportSearchRow): SearchMain {
  const kind = searchKind(r);
  const p = pctText(r.search_change_pct, r.search_reason);
  switch (kind) {
    case "small_base": return { kind, text: "급증(기준값 작음)", known: true, bar: null, small: p.known ? p.text : null };
    case "volume_only": return { kind, text: volumeText(r) ?? "값 없음", known: false, bar: null, small: null };
    case "none": return { kind, text: r.search_reason || "값 없음", known: false, bar: null, small: null };
    default: return { kind, text: p.text, known: p.known, bar: p.known ? r.search_change_pct : null, small: null };
  }
}

/** 키워드 아래 작은 글 줄들: 월 검색 · 쇼핑 · 기준 설명 */
export function searchNotes(r: TrendReportSearchRow, b: Pick<TrendReportBrief, "target_gender" | "target_ages">): string[] {
  const who = targetText(b.target_gender, b.target_ages);
  const kind = searchKind(r);
  const out: string[] = [];
  const volume = volumeText(r);
  if (volume && kind !== "volume_only") out.push(volume);
  if (r.shopping_change_pct != null) {
    const p = pctText(r.shopping_change_pct, null).text;
    out.push(`쇼핑 클릭(${who}) ${r.shopping_small_base ? `급증(기준값 작음) ${p}` : p}`);
  } else if (r.shopping_reason) {
    // 비었으면 이유를 그대로 (요청 오류 / 그 분야에 없는 키워드 / 값이 너무 적음 / 한도)
    out.push(`쇼핑: ${r.shopping_reason}`);
  }
  if (kind === "all") out.push(r.search_reason || `${who}로 거르면 값이 없음 → 전체 기준`);
  return out;
}

// ---- v2 2 해시태그

export const HASHTAG_HIDDEN_NOTE = "숨긴 것: 표기용 태그(#광고·#제작지원 등)와 매거진 협찬 태그는 세지 않아요";

/** "톤28 · 비플레인 · 라운드랩 외 N곳 · 터진 게시물 M" */
export function hashtagMeta(r: TrendReportHashtagRow, names: Record<string, string | null | undefined> = {}): string {
  const shown = (r.used_by ?? []).map((id) => names[id]?.trim() || `@${id}`);
  const parts: string[] = [];
  if (shown.length) {
    const more = r.accounts - shown.length;
    parts.push(more > 0 ? `${shown.join(" · ")} 외 ${more}곳` : shown.join(" · "));
  }
  parts.push(`터진 게시물 ${r.burst_posts.toLocaleString("ko-KR")}`);
  return parts.join(" · ");
}

// ---- v2 4 경쟁사

/** 최고 게시물 배수 큰 순 (없으면 뒤) */
export function sortCompetitors(rows: TrendReportCompetitorRow[]): TrendReportCompetitorRow[] {
  return rows.map((r, i) => ({ r, i }))
    .sort((x, y) => (y.r.best_post?.ratio ?? -1) - (x.r.best_post?.ratio ?? -1) || x.i - y.i).map((x) => x.r);
}

/** "잘 된 건 전부 릴스 (평소 35%)" 같은 문장 (코드 템플릿) */
export function traitSentence(t: TrendTraitStructured): string {
  switch (t.label) {
    case "릴스 비율": return t.value.trim() === "100%" ? `잘 된 건 전부 릴스 (${t.baseline})` : `잘 된 건 릴스 ${t.value} (${t.baseline})`;
    case "첫 줄 질문": return `첫 줄이 질문으로 시작 (${t.value}, ${t.baseline})`;
    case "올린 시간": return `${t.value}에 올림 (${t.baseline})`;
    case "캡션 길이": return `캡션 ${t.value} (${t.baseline})`;
    default: return `${t.label} ${t.value} (${t.baseline})`;
  }
}

/** 카드의 "잘 된 게시물은" 줄들: 구조화 값이 있으면 문장, 없으면 옛 traits 글 */
export function traitLines(r: TrendReportCompetitorRow): string[] {
  return r.traits_structured?.length ? r.traits_structured.map(traitSentence) : r.traits;
}

/** 경쟁사 위 한 줄 요약: 가장 많은 경쟁사가 공유한 특징 (같으면 릴스 먼저). 없으면 null */
export function competitorSummary(rows: TrendReportCompetitorRow[]): string | null {
  const total = rows.length;
  if (!total) return null;
  const reels = rows.filter((r) => r.best_post?.media_product_type === "REELS").length;
  const count = (label: TrendTraitStructured["label"]) => rows.filter((r) => r.traits_structured?.some((t) => t.label === label)).length;
  const cands: [number, string][] = [
    [reels, `경쟁사 ${total}곳 중 ${reels}곳의 최고 게시물이 릴스`],
    [count("첫 줄 질문"), `경쟁사 ${total}곳 중 ${count("첫 줄 질문")}곳의 잘 된 게시물이 첫 줄을 질문으로 시작`],
    [count("올린 시간"), `경쟁사 ${total}곳 중 ${count("올린 시간")}곳의 잘 된 게시물이 비슷한 시간대에 올라옴`],
    [count("캡션 길이"), `경쟁사 ${total}곳 중 ${count("캡션 길이")}곳의 잘 된 게시물 캡션 길이가 평소와 다름`],
  ];
  let best = cands[0];
  for (const c of cands) if (c[0] > best[0]) best = c;
  return best[0] > 0 ? best[1] : null;
}

// ---- v2 근거와 한계

export const LIMIT_GROUPS = [
  { key: "method", label: "계산 방식", cls: "bg-emerald-50 border-emerald-100 text-emerald-800" },
  { key: "cannot_see", label: "볼 수 없는 것", cls: "bg-amber-50 border-amber-100 text-amber-800" },
  { key: "this_report", label: "이번 주의", cls: "bg-red-50 border-red-100 text-red-800" },
] as const;

/** 한계 세 묶음. limit_groups가 없거나 모두 비면 null (옛 저장본 → 평평한 limits 목록) */
export function limitGroups(r: Pick<TrendReport, "limit_groups">): { label: string; cls: string; items: string[] }[] | null {
  const g = r.limit_groups;
  if (!g) return null;
  const out = LIMIT_GROUPS.map((x) => ({ label: x.label, cls: x.cls, items: g[x.key] ?? [] })).filter((x) => x.items.length > 0);
  return out.length ? out : null;
}
