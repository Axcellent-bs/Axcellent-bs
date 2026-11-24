// 고객·시장 분석 탭(components/data/SegmentAnalysis.tsx)이 그리는 값. 서버가 준 숫자를 글자로만 바꾼다(새 지표를 계산하지 않는다).
// 순수 함수라 node --test로 시험한다 (segmentView.test.ts). 코드값(속성 key·값 code·장르 code·source_id·proposal_id)은 글자로 내보내지 않는다.
// 선택지(속성·값·장르·분류·자주 묻는 질문·기간)는 segment_options 응답에서만 읽는다 (코드에 고정하지 않음).
// 품목 이름(책·게임 등)은 segment_options의 item_noun, 바깥 출처 이름은 응답 label에서만 온다 (회사 낱말을 박지 않는다).
import type {
  BasketLevel, BasketPairsOutput, CoverageRow, EventProposalItem, EventProposalOutput, EventProposalSendOutput, ExternalResult,
  FavoriteItem, MarketCoverageOutput, RetentionCohort, RetentionOutput, SalesBasis, SalesQuality, SegmentFavoritesOutput,
  SegmentOptionsOutput, SegmentPeriod, SegmentProfileOutput, SegmentQuestionOutput,
} from "./data.ts";

export const DATA_NOTE = "고객 속성은 시연용 가상 데이터, 바깥 시장 숫자는 공공 데이터";
export const PUBLIC_NOTE = "바깥 시장 숫자는 공공 데이터";

/** 맨 위 안내 한 줄: 가짜 출처(data_source 있음)일 때만 "시연용 가상 데이터"를 말한다. 선택지를 아직 못 받았으면 없음 */
export function dataNote(options: { badge: string | null } | null): string | null {
  if (!options) return null;
  return options.badge ? DATA_NOTE : PUBLIC_NOTE;
}
export const SUPPRESSED = "표본 부족";

const withComma = (n: number) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
export const people = (n: number) => `${withComma(n)}명`;
export const won = (n: number) => `${withComma(n)}원`;

/** 0..1 → 퍼센트. 1% 미만은 소수 둘째 자리까지 ("0.04%"), 그 외는 첫째 자리까지 ("31%", "12.5%") */
export function pct(v: number): string {
  const digits = v * 100 < 1 ? 100 : 10;
  const p = Math.round(v * 100 * digits) / digits;
  return `${p}%`;
}

/** 배수 "1.5배" · "1.0배" (늘 소수 첫째 자리까지) */
export function times(v: number): string {
  return `${(Math.round(v * 10) / 10).toFixed(1)}배`;
}

/** 이 배수 이상이면 "특히 많이 산다"로 초록 강조 (1.0~1.2는 차이가 작아 강조하지 않음) */
export const HIGH_LIFT = 1.2;
export const isHigh = (lift: number | null | undefined) => lift != null && lift >= HIGH_LIFT;

const DEFAULT_NOUN = "상품";

const PERIOD_LABEL: Record<SegmentPeriod, string> = {
  "1d": "최근 1일",
  "7d": "최근 7일",
  "30d": "최근 30일",
  "90d": "최근 3개월",
  "365d": "최근 1년",
};
export const periodLabel = (p: SegmentPeriod) => PERIOD_LABEL[p] ?? "기간 모름";

// ---- 칸 상태: skill이 없거나 실패하면 그 칸만 "준비 중"

export type SectionState = { kind: "missing" } | { kind: "failed"; message: string };

/** 서버가 skill을 모르면(카드에 없음 FORBIDDEN_SKILL, 게이트웨이 403 skill_not_allowed) "준비 중", 그 밖은 실패 */
export function sectionState(error: string): SectionState {
  if (/FORBIDDEN_SKILL|skill_not_allowed|\(403\)|\(404\)/.test(error)) return { kind: "missing" };
  return { kind: "failed", message: "지금은 받지 못했어요. 잠시 뒤 다시 시도해 주세요." };
}

// ---- 공통: 가짜 데이터 배지, 부족 이유, 기준

/** 근거(basis.data_source 또는 선택지 data_source)에 가짜 원천 표시가 있으면 배지 글자 */
export function fakeBadge(source: { basis?: SalesBasis | null; data_source?: string | null } | null | undefined): string | null {
  const v = source?.basis?.data_source ?? source?.data_source ?? null;
  return v ? "가짜 데이터로 본 결과" : null;
}

const HANGUL = /[가-힣]/;

/** 품질에서 사람이 읽을 사실 (영어 코드는 내보내지 않는다) */
function qualityFacts(q: SalesQuality | undefined): string[] {
  if (!q) return [];
  const out = [`기간 안 주문 행 ${withComma(q.rows_in_period)}개`];
  if (q.dropped_rows > 0) {
    const why = Object.entries(q.dropped_reasons ?? {}).filter(([k, n]) => HANGUL.test(k) && n > 0).map(([k, n]) => `${k} ${n}개`);
    out.push(`버린 행 ${q.dropped_rows}개${why.length ? ` (${why.join(", ")})` : ""}`);
  }
  if ((q.unknown_genres ?? []).length > 0) out.push(`이름이 없는 장르 ${q.unknown_genres!.length}개`);
  const unmapped = Object.values(q.unmapped_attribute_values ?? {}).reduce((n, xs) => n + xs.length, 0);
  if (unmapped > 0) out.push(`설정에 없는 고객 속성 값 ${unmapped}개`);
  return out;
}

export interface GateView { kind: "gate"; reasons: string[]; facts: string[] }

function gate(out: { insufficient_reasons: string[]; quality?: SalesQuality }): GateView {
  const reasons = out.insufficient_reasons.length ? [...out.insufficient_reasons] : ["데이터가 부족해 계산하지 않았어요"];
  return { kind: "gate", reasons, facts: qualityFacts(out.quality) };
}

// ---- 선택지 (segment_options)

export interface Choice { value: string; label: string }
export interface AttributeChoice { key: string; label: string; values: Choice[] }
export interface QuickChip { label: string; filters: Record<string, string> }
export interface TargetChoice { value: string; label: string; group: string }
export interface OptionsView {
  attributes: AttributeChoice[];
  chips: QuickChip[];
  targets: TargetChoice[];
  periods: Choice[];
  coverage: Choice[]; // value = 속성 key를 ","로 이은 것
  itemNoun: string; // 화면 품목 이름 (예: 책·게임)
  badge: string | null;
}

/** 타깃 값 "<kind>:<code>" ↔ 분해 */
export const targetValue = (kind: "genre" | "category", code: string) => `${kind}:${code}`;
export function parseTarget(v: string): { kind: "genre" | "category"; code: string } | null {
  const i = v.indexOf(":");
  if (i < 0) return null;
  const kind = v.slice(0, i);
  return kind === "genre" || kind === "category" ? { kind, code: v.slice(i + 1) } : null;
}

export function optionsView(o: SegmentOptionsOutput): OptionsView {
  const labelOf = new Map(o.attributes.map((a) => [a.key, a.label]));
  const validKey = new Set(o.attributes.map((a) => a.key));
  return {
    attributes: o.attributes.map((a) => ({ key: a.key, label: a.label, values: a.values.map((v) => ({ value: v.code, label: v.label })) })),
    // 설정에 없는 속성을 가리키는 칩은 뺀다
    chips: o.quick_questions.filter((q) => Object.keys(q.filters).every((k) => validKey.has(k))).map((q) => ({ label: q.label, filters: { ...q.filters } })),
    targets: [
      ...o.genres.map((g) => ({ value: targetValue("genre", g.code), label: g.label, group: "장르" })),
      ...o.categories.map((c) => ({ value: targetValue("category", c.code), label: c.label, group: "분류" })),
    ],
    periods: o.periods.filter((p) => p in PERIOD_LABEL).map((p) => ({ value: p, label: PERIOD_LABEL[p] })),
    coverage: o.population_keys
      .filter((ks) => ks.length > 0 && ks.every((k) => labelOf.has(k)))
      .map((ks) => ({ value: ks.join(","), label: ks.map((k) => labelOf.get(k)!).join(" × ") })),
    itemNoun: o.item_noun?.trim() || DEFAULT_NOUN,
    badge: fakeBadge(o),
  };
}

// ---- 0) 글로 묻기 (segment_question)

export type QuestionView =
  | { kind: "ok"; text: string; filters: Record<string, string>; period: SegmentPeriod | null }
  | { kind: "unparsed"; text: string; reason: string | null };

/** 서버가 고른 code를 화면 선택지와 다시 맞춘다 (선택지에 없는 속성·값은 버림). 하나도 안 남으면 unparsed */
export function questionView(out: SegmentQuestionOutput, options: Pick<OptionsView, "attributes" | "periods">): QuestionView {
  const filters = Object.fromEntries(Object.entries(out.status === "ok" ? out.filters : {})
    .filter(([k, v]) => options.attributes.some((a) => a.key === k && a.values.some((x) => x.value === v))));
  if (Object.keys(filters).length === 0) {
    return { kind: "unparsed", text: "어느 집단인지 골라 주세요", reason: out.reason && HANGUL.test(out.reason) ? out.reason : null };
  }
  const period = out.period && options.periods.some((p) => p.value === out.period) ? out.period : null;
  const label = out.understood_label && HANGUL.test(out.understood_label) ? out.understood_label : filterLabel(filters, options.attributes);
  return { kind: "ok", text: `이렇게 읽었어요: ${label}`, filters, period };
}

/** 칩·드롭다운 값으로 고른 조건 → 화면 이름 ("20대 · 여성"). 모르는 값은 빼고 code를 보이지 않는다 */
export function filterLabel(filters: Record<string, string>, attributes: AttributeChoice[]): string {
  return attributes
    .map((a) => (a.key in filters ? a.values.find((v) => v.value === filters[a.key])?.label : undefined))
    .filter((x): x is string => !!x)
    .join(" · ");
}

// ---- 1) 물어보기 (segment_favorites)

export interface FavoriteRow { label: string; detail: string; suppressed: boolean; high: boolean }
export interface ExternalView { title: string; meta: string; state: "ok" | "note"; note: string | null; rows: { label: string; detail: string }[] }
export interface FavoriteList { title: string; rows: FavoriteRow[] }
export interface FavoritesOkView {
  kind: "ok";
  header: string;
  genres: FavoriteList; // 특히 많이 사는 장르 (배수 높은 순 5개)
  products: FavoriteList; // 많이 산 {item_noun} (서버 순서 = 구매 비율 순 5개)
  external: ExternalView[]; // 결과를 받은 바깥 출처
  externalNote: string | null; // 실패한 출처 (같은 이유는 한 번) · 확인한 출처 n/m
  summary: string;
  badge: string | null;
}
export const TOP_ROWS = 5;
export type FavoritesView = GateView | FavoritesOkView;

const EXTERNAL_NOTE: Record<Exclude<ExternalResult["status"], "ok">, string> = {
  key_missing: "키 등록 필요",
  limit: "오늘 조회 한도에 닿음",
  error: "읽지 못함",
};

export function externalView(e: ExternalResult): ExternalView {
  const title = e.label || "바깥 출처";
  const meta = [e.condition_label, e.period_label].filter(Boolean).join(" · ");
  if (e.status !== "ok") {
    const why = HANGUL.test(e.reason) ? ` · ${e.reason}` : "";
    return { title, meta, state: "note", note: `${EXTERNAL_NOTE[e.status] ?? "읽지 못함"}${why}`, rows: [] };
  }
  const rows = e.items.map((it) => ({
    label: `${it.rank != null ? `${it.rank}. ` : ""}${it.name}`,
    detail: it.value == null ? "" : `${withComma(it.value)}${it.unit}`,
  }));
  return { title, meta, state: rows.length ? "ok" : "note", note: rows.length ? null : "결과가 없어요", rows };
}

function favoriteRow(f: FavoriteItem): FavoriteRow {
  if (f.suppressed) return { label: f.label, detail: SUPPRESSED, suppressed: true, high: false };
  const parts = [
    f.share != null ? `구매자의 ${pct(f.share)}` : null,
    f.lift != null ? `전체 대비 ${times(f.lift)}` : null,
    f.buyers != null ? people(f.buyers) : null,
  ].filter(Boolean);
  return { label: f.label, detail: parts.join(" · "), suppressed: false, high: isHigh(f.lift) };
}

/** 배수 큰 순 (같으면 구매 비율 큰 순, 서버 순서). 표본 부족은 뒤에 */
function byLift(items: FavoriteItem[]): FavoriteItem[] {
  return items.map((f, i) => ({ f, i })).sort((a, b) =>
    Number(a.f.suppressed) - Number(b.f.suppressed)
    || (b.f.lift ?? -1) - (a.f.lift ?? -1)
    || (b.f.share ?? -1) - (a.f.share ?? -1)
    || a.i - b.i).map(({ f }) => f);
}

/** 바깥 출처: 결과를 받은 것은 그대로, 실패는 (출처·상태·이유)가 같으면 한 번만 + "확인한 출처 n/m" (출처 = source_id) */
export function externalSummary(results: ExternalResult[]): { shown: ExternalView[]; note: string | null } {
  const shown = results.filter((e) => e.status === "ok").map(externalView);
  const failed = new Map<string, string>();
  for (const e of results) {
    if (e.status === "ok") continue;
    const key = `${e.source_id}|${e.status}|${e.reason}`;
    if (failed.has(key)) continue;
    const v = externalView(e);
    failed.set(key, `${v.title} ${v.note}`);
  }
  if (failed.size === 0) return { shown, note: null };
  const sources = new Set(results.map((e) => e.source_id));
  const okSources = new Set(results.filter((e) => e.status === "ok").map((e) => e.source_id));
  return { shown, note: [...failed.values(), `확인한 출처 ${okSources.size}/${sources.size}`].join(" · ") };
}

export function favoritesView(out: SegmentFavoritesOutput, itemNoun: string = DEFAULT_NOUN): FavoritesView {
  if (out.status === "insufficient") return gate(out);
  const who = out.filter_label || "고른 집단";
  const count = out.segment_suppressed ? SUPPRESSED : out.segment_buyers != null ? `구매자 ${people(out.segment_buyers)}` : "구매자 수 모름";
  // 분류(category: 큰 묶음)는 이 칸에서 뺀다. 장르는 배수 순, 품목은 서버 순서(구매 비율 순)
  const genres = out.segment_suppressed ? [] : byLift(out.internal.filter((f) => f.kind === "genre")).slice(0, TOP_ROWS);
  const products = out.segment_suppressed ? [] : out.internal.filter((f) => f.kind === "product").slice(0, TOP_ROWS);
  const ext = externalSummary(out.external);
  return {
    kind: "ok",
    header: `회사 사이트 · ${who} ${count} (${periodLabel(out.period)})`,
    genres: { title: "특히 많이 사는 장르", rows: genres.map(favoriteRow) },
    products: { title: `많이 산 ${itemNoun}`, rows: products.map(favoriteRow) },
    external: ext.shown,
    externalNote: ext.note,
    summary: favoritesSummary(out),
    badge: fakeBadge(out),
  };
}

/** 첫 문장 = 배수 1위 장르, 둘째 문장 = 구매 비율 1위 장르 (서버 숫자를 글자로만) */
export function favoritesSummary(out: SegmentFavoritesOutput): string {
  if (out.segment_suppressed) return "";
  const genres = out.internal.filter((f) => f.kind === "genre" && !f.suppressed);
  const lifted = byLift(genres.filter((f) => f.lift != null))[0];
  const bought = genres.filter((f) => f.share != null).map((f, i) => ({ f, i }))
    .sort((a, b) => (b.f.share ?? 0) - (a.f.share ?? 0) || a.i - b.i)[0]?.f;
  const parts: string[] = [];
  if (lifted) parts.push(`특히 많이 사는 장르: ${lifted.label} (전체 대비 ${times(lifted.lift!)}).`);
  if (bought) parts.push(`가장 많이 산 장르: ${bought.label} (구매자의 ${pct(bought.share!)}).`);
  return parts.join(" ");
}

// ---- 2) ○○은 누가 사나 (segment_profile)

export interface ShareRow { label: string; target: string; targetPct: number; overall: string; lift: string; high: boolean; suppressed: boolean }
export interface ProfileOkView {
  kind: "ok";
  title: string;
  header: string;
  tables: { label: string; rows: ShareRow[]; unknown: string | null }[];
  summary: string;
  badge: string | null;
}
export type ProfileView = GateView | ProfileOkView;

export function profileView(out: SegmentProfileOutput): ProfileView {
  if (out.status === "insufficient") return gate(out);
  const name = out.target_label || "고른 대상";
  const target = out.target_suppressed ? SUPPRESSED : out.target_buyers != null ? `구매자 ${people(out.target_buyers)}` : "구매자 수 모름";
  const overall = out.overall_buyers != null ? ` · 전체 ${people(out.overall_buyers)}` : "";
  return {
    kind: "ok",
    title: `${name}은(는) 누가 사나`,
    header: `${name} ${target}${overall} (${periodLabel(out.period)})`,
    tables: out.target_suppressed ? [] : out.attributes.map((a) => ({
      label: a.label,
      rows: a.values.map((v) => v.suppressed
        ? { label: v.label, target: SUPPRESSED, targetPct: 0, overall: "-", lift: "-", high: false, suppressed: true }
        : {
          label: v.label,
          target: v.target_share != null ? pct(v.target_share) : "-",
          targetPct: v.target_share != null ? Math.max(0, Math.min(100, Math.round(v.target_share * 1000) / 10)) : 0,
          overall: v.overall_share != null ? pct(v.overall_share) : "-",
          lift: v.lift != null ? times(v.lift) : "-",
          high: isHigh(v.lift),
          suppressed: false,
        }),
      unknown: a.unknown_buyers ? `${a.label} 모름 ${people(a.unknown_buyers)}` : null,
    })),
    summary: out.summary,
    badge: fakeBadge(out),
  };
}

// ---- 2-1) 함께 사는 장르·품목 (basket_pairs). 단위 = 같은 사람이 기간 안에 둘 다 산 것

export interface PairRowView { pair: string; together: string; confidence: string; lift: string; high: boolean; suppressed: boolean }
export interface BasketOkView { kind: "ok"; header: string; rows: PairRowView[]; empty: string | null; summary: string; badge: string | null }
export type BasketView = GateView | BasketOkView;

/** 단위 이름: 장르 또는 화면 품목 이름 (item_noun) */
export const basketNoun = (level: BasketLevel, itemNoun: string = DEFAULT_NOUN) => (level === "genre" ? "장르" : itemNoun);

export function basketView(out: BasketPairsOutput, attributes: AttributeChoice[], itemNoun: string = DEFAULT_NOUN): BasketView {
  if (out.status === "insufficient") return gate(out);
  const who = filterLabel(out.filters, attributes) || "전체 고객";
  const rows = out.pairs.map((p): PairRowView => {
    const pair = `${p.a_label} + ${p.b_label}`;
    if (p.suppressed) return { pair, together: SUPPRESSED, confidence: "-", lift: "-", high: false, suppressed: true };
    return {
      pair,
      together: p.together_buyers != null ? people(p.together_buyers) : "-",
      confidence: p.confidence != null ? `${p.a_label} 구매자의 ${pct(p.confidence)}` : "-",
      lift: p.lift != null ? times(p.lift) : "-",
      high: isHigh(p.lift),
      suppressed: false,
    };
  });
  return {
    kind: "ok",
    header: `${who} · 함께 사는 ${basketNoun(out.level, itemNoun)} (${periodLabel(out.period)})`,
    rows,
    empty: rows.length ? null : `${who}에서 함께 산 사람이 충분한 쌍이 없어요`,
    summary: out.summary,
    badge: fakeBadge(out),
  };
}

// ---- 3) 우리 고객은 어느 층에 몰려 있나 (market_coverage: 고객 구성 지수)

export type IndexTone = "high" | "low" | "normal";
export interface CoverageRowView {
  label: string;
  bands: string | null; // 합산한 인구 구간 (population_note)
  buyers: string;
  buyerShare: string;
  populationShare: string;
  buyerPct: number; // 막대 길이 0..100
  populationPct: number;
  index: string;
  tone: IndexTone;
  note: string | null; // 지수를 내지 않은 이유 (표본 부족·응답 안 함 등)
}
export interface CoverageOkView { kind: "ok"; rows: CoverageRowView[]; summary: string; source: string | null; badge: string | null }
/** 인구를 하나도 못 붙였으면(모두 인구 없음, 또는 모두 ok 아님 + 같은 이유): 안내 한 줄 + 첫 속성(줄) × 둘째 속성(칸) 구매자 수 표 하나 */
export interface BuyersTableView {
  kind: "buyers";
  note: string;
  corner: string; // 예: "연령대 \ 성별"
  columns: string[];
  rows: { label: string; cells: string[] }[];
  badge: string | null;
}
export type CoverageView = GateView | CoverageOkView | BuyersTableView;

function buyersCell(r: CoverageRow | undefined): string {
  if (!r) return "-";
  if (r.suppressed) return SUPPRESSED;
  return r.buyers != null ? people(r.buyers) : "-";
}

/** 서버 이유에서 사람이 읽는 부분만: 한글 설명이 없으면 null. "UNAUTHORIZED: " 같은 오류 코드와
 * KOSIS_API_KEY 같은 설정 이름은 뺀다 (출처 이름 KOSIS 같은 낱말은 남긴다) */
export function readableReason(text: string): string | null {
  if (!HANGUL.test(text)) return null;
  return text
    .replace(/\b[A-Z][A-Z_]{2,}:\s*/g, "")
    .replace(/\b[A-Z0-9]+(?:_[A-Z0-9]+)+\b/g, "")
    .replace(/\s+/g, " ")
    .replace(/\(\s+/g, "(")
    .replace(/\s+\)/g, ")")
    .trim();
}

const COVERAGE_NOTE = {
  missing: "인구 통계를 아직 받지 못해 구성 지수 대신 우리 구매자 수(최근 1년)만 보여요",
  error: "인구 통계를 읽지 못해 구성 지수 대신 우리 구매자 수(최근 1년)만 보여요",
};

/** 지수 색: 120 이상 초록(많이 몰림), 80 미만 빨강(적음), 그 사이 기본색 */
export const INDEX_HIGH = 120;
export const INDEX_LOW = 80;
export const indexTone = (v: number | null | undefined): IndexTone =>
  v == null ? "normal" : v >= INDEX_HIGH ? "high" : v < INDEX_LOW ? "low" : "normal";

const NOT_IN_STATS = "인구 통계에 없는 값";
const barPct = (v: number | null | undefined) => (v != null ? Math.max(0, Math.min(100, Math.round(v * 1000) / 10)) : 0);

/** 카드 아래 출처 한 줄. 표 이름·기준 달은 서버 문자열(population_source) 하나만 쓰고 기준 해를 따로 붙이지 않는다.
 * 나이 범위·구간 폭은 서버가 KOSIS 이름으로 만든 population_note에서 읽는다 (회사 낱말을 박지 않는다) */
export function coverageSource(out: MarketCoverageOutput, attributes: AttributeChoice[] = []): string | null {
  const source = out.rows.find((r) => r.population_source)?.population_source;
  if (!source) return null;
  const notes = out.rows.map((r) => r.population_note ?? "").filter(Boolean);
  const lows = notes.map((n) => Number(/^(\d+)/.exec(n)?.[1])).filter((n) => Number.isFinite(n));
  const band = notes.map((n) => /(\d+)~(\d+)세/.exec(n)).find((m) => m);
  const from = lows.length ? `${Math.min(...lows)}세 이상` : null;
  const labels = out.attribute_keys.map((k) => attributes.find((a) => a.key === k)?.label).filter((x): x is string => !!x);
  // 인구 통계에 없는 값(예: 응답 안 함): 그 값이 든 줄이 모두 그 이유로 인구가 없을 때
  const missing: string[] = [];
  for (const a of attributes.filter((x) => out.attribute_keys.includes(x.key))) {
    for (const v of a.values) {
      const rows = out.rows.filter((r) => r.values[a.key] === v.value);
      if (rows.length && rows.every((r) => r.population == null && r.reason.includes(NOT_IN_STATS))) missing.push(`'${v.label}'`);
    }
  }
  return [
    `출처: ${source}`,
    from,
    band ? `각 묶음은 ${Number(band[2]) - Number(band[1]) + 1}세별 구간 합산` : null,
    labels.length ? `비중의 분모는 ${labels.join("·")}을 답한 구매자와 ${from ?? "표의"} 인구` : null,
    missing.length ? `${missing.join("·")}은 인구에 없어 지수를 내지 않음` : null,
  ].filter(Boolean).join(" · ");
}

function buyersTable(out: MarketCoverageOutput, attributes: AttributeChoice[]): BuyersTableView | null {
  const attrs = out.attribute_keys.map((k) => attributes.find((a) => a.key === k));
  if (attrs.length === 0 || attrs.length > 2 || attrs.some((a) => !a)) return null;
  const [rowAttr, colAttr] = attrs as AttributeChoice[];
  const find = (rv: string, cv?: string) => out.rows.find((r) =>
    r.values[rowAttr.key] === rv && (colAttr == null || r.values[colAttr.key] === cv));
  const reasons = [...new Set(out.rows.map((r) => readableReason(r.reason)).filter((x): x is string => !!x))];
  const base = out.rows.every((r) => r.status === "error") ? COVERAGE_NOTE.error : COVERAGE_NOTE.missing;
  return {
    kind: "buyers",
    note: `${base}${reasons.length === 1 ? ` · ${reasons[0]}` : ""}`,
    corner: colAttr ? `${rowAttr.label} \\ ${colAttr.label}` : rowAttr.label,
    columns: colAttr ? colAttr.values.map((v) => v.label) : ["우리 구매자"],
    rows: rowAttr.values.map((rv) => ({
      label: rv.label,
      cells: colAttr ? colAttr.values.map((cv) => buyersCell(find(rv.value, cv.value))) : [buyersCell(find(rv.value))],
    })),
    badge: fakeBadge(out),
  };
}

/** 지수 높은 순. 지수가 없는 묶음(숨김·응답 안 함·인구 없음)은 아래 (서버 순서 유지) */
const indexOrder = (r: CoverageRow) => (r.index != null ? r.index : -Infinity);

export function coverageView(out: MarketCoverageOutput, attributes: AttributeChoice[] = []): CoverageView {
  if (out.status === "insufficient") return gate(out);
  // 인구를 하나도 못 붙였으면 같은 이유를 줄마다 되풀이하지 않고 한 줄 + 구매자 표로 줄인다:
  // 모든 줄이 인구 없음이거나, 모든 줄이 ok가 아니고(인구 없음·오류) 이유가 모두 같을 때
  const notOk = out.rows.length > 0 && out.rows.every((r) => r.status !== "ok");
  const allMissing = notOk && out.rows.every((r) => r.status === "population_missing");
  const sameReason = notOk && new Set(out.rows.map((r) => r.reason)).size === 1;
  if (allMissing || sameReason) {
    const table = buyersTable(out, attributes);
    if (table) return table;
  }
  const rows = out.rows.map((r, i) => ({ r, i }))
    .sort((a, b) => (indexOrder(b.r) === indexOrder(a.r) ? a.i - b.i : indexOrder(b.r) > indexOrder(a.r) ? 1 : -1))
    .map(({ r }): CoverageRowView => {
      const base = {
        label: r.label, bands: r.population_note || null, buyerShare: "-", populationShare: "-", buyerPct: 0, populationPct: 0,
        index: "-", tone: "normal" as IndexTone,
      };
      if (r.suppressed) return { ...base, buyers: SUPPRESSED, note: null };
      const buyers = r.buyers != null ? people(r.buyers) : "-";
      if (r.status !== "ok" || r.index == null) {
        const why = r.status === "ok" ? "지수를 내지 않았어요"
          : HANGUL.test(r.reason) ? r.reason : r.status === "population_missing" ? "인구 통계를 찾지 못했어요" : "읽지 못했어요";
        return { ...base, buyers, note: why };
      }
      return {
        ...base,
        buyers,
        buyerShare: r.buyer_share != null ? pct(r.buyer_share) : "-",
        populationShare: r.population_share != null ? pct(r.population_share) : "-",
        buyerPct: barPct(r.buyer_share),
        populationPct: barPct(r.population_share),
        index: String(Math.round(r.index)),
        tone: indexTone(r.index),
        note: null,
      };
    });
  return { kind: "ok", rows, summary: out.summary, source: coverageSource(out, attributes), badge: fakeBadge(out) };
}

// ---- 4) 다음 달 이벤트 제안 (event_proposal · event_proposal_send)

/** 한국 시간 기준 다음 달 "YYYY-MM" */
export function nextMonth(now: Date): string {
  const k = new Date(now.getTime() + 9 * 3600_000);
  const y = k.getUTCFullYear();
  const m = k.getUTCMonth() + 2; // 다음 달 (1부터)
  return m > 12 ? `${y + 1}-01` : `${y}-${String(m).padStart(2, "0")}`;
}

export const monthLabel = (month: string) => {
  const m = Number(month.slice(5, 7));
  return m >= 1 && m <= 12 ? `${m}월` : "다음 달";
};

const REASON_TITLE: Record<string, string> = { segment: "누구", season: "언제", conversion: "무엇" };

export interface ProposalView {
  id: string; // proposal_id: 버튼에만 쓰고 화면 글자로는 내보내지 않는다
  title: string;
  summary: string | null;
  reasons: { title: string; text: string; source: string }[];
  effect: string;
  formula: string;
  assumption: boolean;
  basis: string; // 전환율 근거
  cost: string | null;
  limits: string[];
  sent: string | null;
  past: string[];
}
export interface ProposalsOkView {
  kind: "ok"; month: string; items: ProposalView[]; summary: string; empty: string | null; badge: string | null;
  excluded: { title: string; rows: string[] } | null; // "제외한 후보 n건" (접어서 보임)
}
export type ProposalsView = GateView | ProposalsOkView;

export function sentText(s: { outcome: "created" | "duplicate" | "rejected"; proposal_ref: string | null; reason?: string }): string {
  const ref = s.proposal_ref ? ` · ${s.proposal_ref}` : "";
  if (s.outcome === "created") return `기획에 넘김${ref}`;
  if (s.outcome === "duplicate") return `이미 넘김${ref}`;
  return `기획이 받지 않음${s.reason && HANGUL.test(s.reason) ? ` · ${s.reason}` : ""}`;
}

export const sendResultText = (r: EventProposalSendOutput) => sentText(r);

function range(lo: number, hi: number, unit: (n: number) => string): string {
  return lo === hi ? unit(lo) : `${unit(lo)}~${unit(hi)}`;
}

export function proposalView(p: EventProposalItem, month: string): ProposalView {
  const e = p.estimate;
  const what = [p.target_label, p.item_label, p.event_label].filter(Boolean).join(" · ");
  return {
    id: p.proposal_id,
    title: p.title || `${monthLabel(month)} · ${what || "이벤트 제안"}`,
    summary: p.summary,
    // 묶음 할인 제안의 첫 근거는 "a → b 함께 산 배수"라 제목을 "함께 사는 것"으로
    reasons: p.reasons.map((r) => ({
      title: p.bundle && r.kind === "segment" ? "함께 사는 것" : REASON_TITLE[r.kind] ?? "근거", text: r.text, source: r.source,
    })),
    effect: `주문 +${range(e.orders_low, e.orders_high, (n) => `${withComma(n)}건`)} · 매출 +${range(e.revenue_low, e.revenue_high, won)}`,
    formula: e.formula,
    assumption: e.assumption || e.conversion_source === "assumption",
    basis: `전환율 ${range(e.conversion_low, e.conversion_high, pct)} · ${e.conversion_source === "past_campaign" ? "지난 캠페인 기준" : "가정값"}`,
    cost: e.discount_cost_high > 0 ? `할인 비용 ${range(e.discount_cost_low, e.discount_cost_high, won)}` : null,
    limits: [...p.limits],
    sent: p.sent ? sentText(p.sent) : null,
    past: p.past_results.map((r) => {
      const name = r.proposal_ref ?? "지난 제안";
      const expected = r.expected_low != null && r.expected_high != null ? `예상 ${range(r.expected_low, r.expected_high, won)}` : "예상 없음";
      const actual = r.actual != null ? `실제 ${won(r.actual)}` : "실제 측정 전";
      return `${name} · ${expected} · ${actual}`;
    }),
  };
}

export function proposalsView(out: EventProposalOutput): ProposalsView {
  if (out.status === "insufficient") return gate(out);
  return {
    kind: "ok",
    month: monthLabel(out.month),
    items: out.proposals.map((p) => proposalView(p, out.month)),
    summary: out.summary,
    empty: out.proposals.length ? null : "이번 달에 낼 만한 제안이 없어요",
    badge: fakeBadge(out),
    excluded: (out.excluded ?? []).length === 0 ? null : {
      title: `제외한 후보 ${out.excluded!.length}건`,
      rows: out.excluded!.map((x) => {
        const what = [x.target_label, x.item_label].filter(Boolean).join(" · ") || "후보";
        return HANGUL.test(x.reason) ? `${what} — ${x.reason}` : what;
      }),
    },
  };
}

// ---- 5) 다시 사나 · 떠나나 (retention)

export const REPEAT_DAYS = [30, 60, 90] as const;
export const NOT_YET = "아직";

export interface CohortRowView { month: string; customers: string; cells: string[]; suppressed: boolean }
export interface AtRiskRowView { label: string; count: string; detail: string }
export interface RetentionOkView {
  kind: "ok";
  cohortTitle: string;
  columns: string[]; // "30일 안" …
  cohorts: CohortRowView[];
  cohortHint: string;
  riskTitle: string;
  atRisk: AtRiskRowView[];
  riskEmpty: string | null;
  note: string;
  summary: string;
  badge: string | null;
}
export type RetentionView = GateView | RetentionOkView;

/** 첫 구매 달 "YYYY-MM"의 다음 달 1일 0시(한국) + days일이 now 이전이면 그 칸 기간이 다 지남 */
export function cohortDone(month: string, days: number, now: Date): boolean {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7)); // 1..12
  const nextFirstKst = Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1) - 9 * 3600_000;
  return nextFirstKst + days * 86400_000 <= now.getTime();
}

function cohortRow(c: RetentionCohort, now: Date): CohortRowView {
  if (c.suppressed || c.customers == null) {
    return { month: monthLabel(c.cohort_month), customers: SUPPRESSED, cells: REPEAT_DAYS.map(() => "-"), suppressed: true };
  }
  const values = [c.r30, c.r60, c.r90];
  return {
    month: monthLabel(c.cohort_month),
    customers: people(c.customers),
    // 비어 있으면: 기간이 아직이면 "아직", 다 지났으면 서버가 숨긴 칸(다시 산 사람이 기준 미만)
    cells: REPEAT_DAYS.map((d, i) => values[i] != null ? pct(values[i]!) : cohortDone(c.cohort_month, d, now) ? SUPPRESSED : NOT_YET),
    suppressed: false,
  };
}

export function retentionView(out: RetentionOutput, now: Date): RetentionView {
  if (out.status === "insufficient") return gate(out);
  return {
    kind: "ok",
    cohortTitle: "첫 구매 달별 재구매율 (코호트)",
    columns: REPEAT_DAYS.map((d) => `${d}일 안`),
    cohorts: out.cohorts.map((c) => cohortRow(c, now)),
    cohortHint: `"${NOT_YET}" = 그 기간이 다 지나지 않음 · "${SUPPRESSED}" = 묶음이 작아 숨김`,
    riskTitle: `떠날 것 같은 단골 (${out.churn_days}일 넘게 안 삼)`,
    atRisk: out.at_risk.filter((r) => !r.suppressed && r.at_risk != null).map((r) => ({
      label: r.label || "고른 묶음",
      count: people(r.at_risk!),
      detail: [
        r.share != null ? `단골의 ${pct(r.share)}가 ${out.churn_days}일 넘게 안 삼` : null,
        r.top_item_label ? `주로 사던 것: ${r.top_item_label}` : null,
      ].filter(Boolean).join(" · "),
    })),
    riskEmpty: out.at_risk.some((r) => !r.suppressed) ? null : "보여 줄 만큼 큰 묶음이 없어요 (표본 부족)",
    note: `"떠남" 기준 ${out.churn_days}일은 회사 설정(가정)입니다. 고객 한 명 한 명은 보여 주지 않고 묶음 수만 봅니다.`,
    summary: out.summary,
    badge: fakeBadge(out),
  };
}
