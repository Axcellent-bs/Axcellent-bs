// 트렌드 분석 보고서를 "한눈에" 읽히게 하는 순수 계산 (11/23, components/data/TrendReportView.tsx). node --test (trendReportRead.test.ts)
// - 머리: 의뢰 한 줄 요약(briefLine) · 결론 카드 핵심 숫자 3칸(keyNumbers) · 기획에 넘길 재료(materialCells, 백엔드가 materials를 주면)
// - 근거 섹션: 접는 카드 머리 한 줄(sectionSummary, 이미 있는 숫자만) · 기본 펼침(defaultOpenSections, 결론이 기대는 근거의 섹션만)
// - 검색 표(searchTable): 키워드 한 줄씩, 값 없는 키워드는 한 줄로 합침
// 숫자는 서버 값 그대로 글자로만 바꾼다
import type { TrendEvidenceItem, TrendReport, TrendReportBrief, TrendReportMaterial, TrendReportSearchRow } from "./data.ts";
import {
  GENDER_LABEL, SECTION_STATUS, competitorChip, pctText, searchKind, searchMain, sortSearchRows, volumeText,
} from "./trendReportView.ts";

// ---- 머리: 의뢰 한 줄 요약

export interface BriefItem { label: string; value: string }

/** "목표 브랜드 인지도 · 누구에게 20·30대 · 무엇을 광고 영상 · 주제 트렌드 밈 · 경쟁사 6곳". 빈 칸은 뺀다 */
export function briefLine(b: TrendReportBrief): BriefItem[] {
  const out: BriefItem[] = [];
  if (b.goal?.trim()) out.push({ label: "목표", value: b.goal.trim() });
  const ages = b.target_ages.length ? `${b.target_ages.join("·")}대` : "";
  const who = [b.target_gender ? GENDER_LABEL[b.target_gender] : "", ages].filter(Boolean).join(" ") || "전체";
  out.push({ label: "누구에게", value: who });
  const format = (b.format_parts ?? []).filter(Boolean).join(" · ") || b.format?.trim() || "";
  if (format) out.push({ label: "무엇을", value: format });
  if (b.theme?.trim()) out.push({ label: "주제", value: b.theme.trim() });
  if (b.competitors.length) out.push({ label: "경쟁사", value: `${b.competitors.length}곳` });
  return out;
}

// ---- 결론 카드: 핵심 숫자 3칸

export interface KeyNumber { id: string; big: string; label: string; text: string }

// 짧은 근거("소설 검색 +361%", "#북토크 2곳 사용", "민음사 릴스 좋아요 ×3.7")에서 크게 쓸 숫자 하나
const BIG = /[+\-−]?\d[\d,.]*%|×\s?\d[\d,.]*|[+\-−]?\d[\d,.]*\s?(?:곳|개|배|회|번)/g;

/** 근거 칩(short)을 "큰 숫자 + 설명 한 줄"로. 숫자가 없으면 short 통째로 크게 */
export function keyNumber(e: TrendEvidenceItem): KeyNumber {
  const found = [...e.short.matchAll(BIG)];
  const hit = found[found.length - 1];
  if (!hit) return { id: e.id, big: e.short, label: "", text: e.text };
  const big = hit[0].replace(/\s/g, "");
  const label = (e.short.slice(0, hit.index) + e.short.slice((hit.index ?? 0) + hit[0].length)).replace(/\s+/g, " ").trim();
  return { id: e.id, big, label, text: e.text };
}

export const keyNumbers = (items: TrendEvidenceItem[]) => items.map(keyNumber);

// ---- 결론 카드: 기획에 넘길 재료 (백엔드 recommendation.materials, 없으면 화면이 콘티를 그대로 그린다)

/** 재료 종류 → 한국어 이름 (모르는 종류는 그대로) */
export const MATERIAL_KIND_LABEL: Record<string, string> = {
  hashtag: "해시태그", format: "형식", topic: "관심 주제", theme: "관심 주제", keyword: "관심 주제", caption: "글쓰기", writing: "글쓰기",
  copy: "글쓰기", hook: "첫 장면", timing: "올릴 시간", music: "음악", meme: "밈", product: "상품",
};
export const materialKindLabel = (kind: string) => MATERIAL_KIND_LABEL[kind] ?? kind;

export interface MaterialCell { key: string; label: string; text: string; evidence: { id: string | null; text: string }[] }

/** 재료 칸들. materials가 없거나 비면 null (옛 보고서 → 콘티) */
export function materialCells(materials: TrendReportMaterial[] | null | undefined): MaterialCell[] | null {
  const list = (materials ?? []).filter((m) => m && m.text?.trim());
  if (!list.length) return null;
  return list.map((m, i) => ({
    key: `${m.kind}:${i}`,
    label: materialKindLabel(m.kind),
    text: m.text,
    evidence: m.evidence_items?.length ? m.evidence_items.map((e) => ({ id: e.id, text: e.text })) : (m.evidence ?? []).map((text) => ({ id: null, text })),
  }));
}

// ---- 근거 섹션: 접기

export type FoldKey = "theme" | "search" | "hashtags" | "content" | "competitors" | "limits";

/** 근거 id("search:1", "shopping:2", "meme:1" …) → 그 근거가 있는 섹션. 모르면 null */
export function sectionOfEvidence(id: string): FoldKey | null {
  const head = id.split(":")[0];
  switch (head) {
    case "theme": return "theme";
    case "search": case "shopping": return "search";
    case "hashtag": return "hashtags";
    case "content": case "meme": return "content";
    case "competitor": return "competitors";
    default: return null;
  }
}

/** 기본 펼침: 결론이 기대는 근거(재료가 있으면 재료 근거, 없으면 콘티 근거)가 가리키는 섹션만. 나머지는 접힘 */
export function defaultOpenSections(rec: Pick<TrendReport["sections"]["recommendation"], "beats" | "materials">): Set<FoldKey> {
  const ids: string[] = [];
  const mats = (rec.materials ?? []).filter((m) => m && m.text?.trim());
  if (mats.length) for (const m of mats) for (const e of m.evidence_items ?? []) ids.push(e.id);
  else for (const b of rec.beats) for (const e of b.evidence_items ?? []) ids.push(e.id);
  const out = new Set<FoldKey>();
  for (const id of ids) { const k = sectionOfEvidence(id); if (k) out.add(k); }
  return out;
}

/** 펼침 상태: 사람이 누른 값이 먼저, 없으면 기본 펼침 */
export function isOpen(key: FoldKey, defaults: Set<FoldKey>, picked: Partial<Record<FoldKey, boolean>>): boolean {
  return picked[key] ?? defaults.has(key);
}

const fix1 = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
const num = (v: number) => v.toLocaleString("ko-KR");

type SummaryReport = Pick<TrendReport, "sections" | "brief" | "limits" | "limit_groups" | "calls">;

/** 접힌 섹션 머리 한 줄 (이미 있는 숫자만). 섹션이 known이 아니면 상태 글자, 줄 게 없으면 null */
export function sectionSummary(key: FoldKey, r: SummaryReport): string | null {
  const s = r.sections;
  if (key === "limits") {
    const caution = r.limit_groups ? r.limit_groups.this_report.length : r.limits.length;
    return `주의 ${num(caution)}개 · 호출 인스타 ${num(r.calls.instagram)} · 네이버 ${num(r.calls.naver)} · AI ${num(r.calls.llm)}`;
  }
  const sec = s[key];
  if (!sec) return null;
  if (sec.status !== "known") return SECTION_STATUS[sec.status]?.label ?? null;
  switch (key) {
    case "theme": {
      const rows = s.theme?.rows ?? [];
      return rows.length ? `후보 ${num(rows.length)}개 · 1위 ${rows[0].name}` : "값이 나온 후보 없음";
    }
    case "search": {
      const rows = sortSearchRows(s.search.rows);
      const known = rows.filter((x) => searchMain(x).known);
      const top = known.slice(0, 2).map((x) => `${x.keyword} ${searchMain(x).text}`);
      const none = rows.length - known.length;
      const parts = [...top, ...(none > 0 ? [`${num(none)}개는 값 없음`] : [])];
      return parts.length ? parts.join(" · ") : "볼 키워드 없음";
    }
    case "hashtags": {
      const h = s.hashtags.rows[0];
      return h ? `#${h.tag.replace(/^#/, "")}${h.is_new ? " 새로 뜸" : ""} · ${num(h.accounts)}곳 사용` : "해시태그 없음";
    }
    case "content": {
      const posts = s.content.posts;
      if (!posts.length) return "갑자기 뜬 게시물 없음";
      const best = Math.max(...posts.map((p) => p.ratio));
      return `갑자기 뜬 게시물 ${num(posts.length)}개 · 최고 ×${fix1(best)}`;
    }
    case "competitors": {
      const rows = s.competitors.rows;
      if (!rows.length) return "읽은 경쟁사 없음";
      const reels = rows.filter((x) => x.best_post?.media_product_type === "REELS").length;
      const parts = [`${num(rows.length)}곳`];
      if (reels) parts.push(`${num(reels)}곳이 최고 게시물로 릴스`);
      const top = rows.filter((x) => x.reels_view_median != null).sort((a, b) => b.reels_view_median! - a.reels_view_median!)[0];
      if (top) {
        const name = competitorChip(top.account, { ...(r.brief.competitor_names ?? {}), ...(top.name ? { [top.account]: top.name } : {}) }).text;
        parts.push(`릴스 조회 중앙값 최고 ${num(top.reels_view_median!)} (${name})`);
      }
      return parts.join(" · ");
    }
  }
  return null;
}

// ---- 검색 표

export interface SearchTableRow {
  key: string;
  /** 근거 행 DOM id용 1부터 번호 (합친 줄은 null) */
  no: number | null;
  keyword: string;
  search: string;
  /** 검색 값 부호 (+면 초록) */
  up: boolean;
  shopping: string;
  memo: string;
}

/** 키워드 · 검색 7일 · 쇼핑 클릭 · 메모. 검색·쇼핑 둘 다 값이 없는 키워드는 맨 아래 한 줄로 합친다 */
export function searchTable(rows: TrendReportSearchRow[]): SearchTableRow[] {
  const out: SearchTableRow[] = [];
  const empty: { keyword: string; reason: string }[] = [];
  for (const r of sortSearchRows(rows)) {
    const m = searchMain(r);
    const shop = r.shopping_change_pct != null ? pctText(r.shopping_change_pct, null).text + (r.shopping_small_base ? " (기준값 작음)" : "") : null;
    if (!m.known && shop == null) { empty.push({ keyword: r.keyword, reason: m.text }); continue; }
    const kind = searchKind(r);
    const memo: string[] = [];
    const volume = volumeText(r);
    if (volume && kind !== "volume_only") memo.push(volume);
    if (!m.known) memo.push(m.text);
    if (kind === "all") memo.push(r.search_reason || "타깃으로 거르면 값이 없어 전체 기준");
    if (shop == null && r.shopping_reason) memo.push(`쇼핑: ${r.shopping_reason}`);
    out.push({
      key: r.keyword, no: rows.indexOf(r) + 1, keyword: r.keyword,
      search: m.known ? `${m.text}${m.small ? ` (${m.small})` : ""}` : "–",
      up: m.known && m.bar != null && m.bar > 0,
      shopping: shop ?? "–", memo: memo.join(" · "),
    });
  }
  if (empty.length) {
    out.push({
      key: "__empty__", no: null, keyword: empty.map((e) => e.keyword).join(" · "), search: "–", up: false, shopping: "–",
      memo: [...new Set(empty.map((e) => e.reason))].join(" / "),
    });
  }
  return out;
}
