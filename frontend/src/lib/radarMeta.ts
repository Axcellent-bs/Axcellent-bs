// 인스타 레이더 계정의 대상층 태그 · 조회 천만 이상 영상의 순수 계산. node --test로 시험한다 (radarMeta.test.ts).
// 숫자·판정은 서버 값 그대로. 여기서는 글자·묶음·셈만 한다. 데이터를 지어내지 않는다.
import type { AgeBand, AudienceGender, MegaViewItem, MegaViews, RadarKind } from "./data.ts";
import type { RadarMetaEntry } from "./instagramRadar.ts";

export const AGE_ORDER: AgeBand[] = ["10s", "20s", "30s", "40s", "50s", "60plus"];
export const AGE_LABEL: Record<AgeBand, string> = {
  "10s": "10대", "20s": "20대", "30s": "30대", "40s": "40대", "50s": "50대", "60plus": "60대 이상",
};
export const GENDER_LABEL: Record<AudienceGender, string> = { male: "남성", female: "여성", all: "남녀" };
export const KIND_LABEL: Record<RadarKind, string> = { meme: "밈·코미디", creator: "크리에이터", industry: "업종", brand: "브랜드" };
export const KIND_ORDER: RadarKind[] = ["meme", "creator", "industry", "brand"];
export const TOPIC_MAX = 20;
/** 60대 이상 계정이 이보다 적으면 표본이 적다고 알린다 */
export const THIN_MIN = 3;
export const THIN_NOTE = "60대 이상은 인스타 표본이 적어 유튜브·검색 비중을 올려요";
export const META_NOTE =
  "대상층 태그는 인스타가 알려 주지 않아요(다른 계정의 팔로워 나이·성별은 공식 API에 없음). 그래서 AI가 계정 소개·게시물로 추정하고, 사람이 확인하면 \"사람 확인\"으로 바뀌어요.";

export type TagSource = "ai" | "human" | null;
export const TAG_SOURCE: Record<"ai" | "human" | "none", { label: string; tone: "ok" | "warn" | "muted" }> = {
  human: { label: "사람 확인", tone: "ok" },
  ai: { label: "AI 추정", tone: "warn" },
  none: { label: "태그 없음", tone: "muted" },
};
export const tagSourceView = (s: TagSource | undefined) => TAG_SOURCE[s ?? "none"];

/** 만 단위 글자: 110만 · 89.8만 · 5만 (1만 미만은 그냥 숫자) */
function man(n: number): string {
  if (n >= 10_000) return `${(n / 10_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만`;
  return n.toLocaleString("ko-KR");
}

/** 팔로워 칸: "110만" · "89.8만" · "8,500", 모르면 "—" */
export function followersLabel(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "—";
  return man(n);
}

export const ageLabel = (a: AgeBand): string => AGE_LABEL[a] ?? a;

/** 나이대를 10대 → 60대 이상 순으로 정렬해 중복 없이 글자로 */
export function ageLabels(ages: readonly AgeBand[] | null | undefined): string[] {
  const set = new Set(ages ?? []);
  return AGE_ORDER.filter((a) => set.has(a)).map(ageLabel);
}

/** 성별 글자: all이 있거나 남·여가 둘 다 있으면 "남녀" 하나, 아니면 남성/여성 */
export function genderLabels(genders: readonly AudienceGender[] | null | undefined): string[] {
  const set = new Set(genders ?? []);
  if (set.has("all") || (set.has("male") && set.has("female"))) return [GENDER_LABEL.all];
  const out: string[] = [];
  if (set.has("male")) out.push(GENDER_LABEL.male);
  if (set.has("female")) out.push(GENDER_LABEL.female);
  return out;
}

export interface RadarRow {
  account: string;
  kind: RadarKind;
  /** 태그를 서버가 줬는지 (목록 안 소속만으로 정한 종류면 false) */
  tagged: boolean;
  kindLabel: string;
  topic: string | null;
  ages: AgeBand[];
  genders: AudienceGender[];
  tagSource: TagSource;
  followers: number | null;
  followersLabel: string;
  /** 제외 이유 (제외가 아니면 null) */
  excluded: string | null;
}

/** 밈·크리에이터이고 팔로워를 알며 기준보다 적으면 제외 이유. 업종·브랜드는 제외하지 않는다 */
export function excludedReason(kind: RadarKind, followers: number | null | undefined, minFollowers: number | null | undefined): string | null {
  if (kind !== "creator" && kind !== "meme") return null;
  if (typeof followers !== "number" || !Number.isFinite(followers)) return null;
  if (typeof minFollowers !== "number" || !Number.isFinite(minFollowers) || minFollowers <= 0) return null;
  if (followers >= minFollowers) return null;
  return `팔로워 ${man(minFollowers)} 미만이라 제외 (${followers.toLocaleString("ko-KR")}명)`;
}

interface RowSource {
  accounts: readonly string[];
  meme_accounts?: readonly string[];
  meta?: Record<string, RadarMetaEntry>;
  min_followers?: number;
}

/** 표 줄: 벤치마크 목록 → 밈 목록 순. 같은 계정이 두 목록에 있으면 한 줄(밈 쪽이 우선).
 *  태그가 없는 계정은 목록 소속으로 종류를 정하고(밈 목록 → 밈, 그 밖 → 업종) 층 칩·팔로워는 비운다 */
export function buildRadarRows(state: RowSource | null | undefined): RadarRow[] {
  if (!state) return [];
  const memeSet = new Set(state.meme_accounts ?? []);
  const seen = new Set<string>();
  const rows: RadarRow[] = [];
  for (const account of [...state.accounts, ...(state.meme_accounts ?? [])]) {
    if (!account || seen.has(account)) continue;
    seen.add(account);
    const m = state.meta?.[account];
    const kind: RadarKind = m?.kind ?? (memeSet.has(account) ? "meme" : "industry");
    const followers = typeof m?.followers === "number" ? m.followers : null;
    rows.push({
      account, kind, tagged: !!m?.kind, kindLabel: KIND_LABEL[kind], topic: m?.topic?.trim() || null,
      ages: m?.ages ?? [], genders: m?.genders ?? [], tagSource: m?.tag_source ?? null,
      followers, followersLabel: followersLabel(followers),
      excluded: excludedReason(kind, followers, state.min_followers),
    });
  }
  return rows;
}

export interface LayerCount { age: AgeBand; label: string; total: number; male: number; female: number }

/** 층별 계정 수 (제외된 계정은 세지 않는다). 성별에 "남녀(all)"가 있으면 남·여 둘 다에 센다 */
export function layerCounts(rows: readonly RadarRow[]): LayerCount[] {
  return AGE_ORDER.map((age) => {
    let total = 0, male = 0, female = 0;
    for (const r of rows) {
      if (r.excluded || !r.ages.includes(age)) continue;
      total += 1;
      const all = r.genders.includes("all");
      if (all || r.genders.includes("male")) male += 1;
      if (all || r.genders.includes("female")) female += 1;
    }
    return { age, label: AGE_LABEL[age], total, male, female };
  });
}

/** "10대 12 · 20대 12 (남 9 · 여 18) · …" 계정이 하나도 없는 층은 0으로 둔다 */
export function layerCountsLine(counts: readonly LayerCount[]): string {
  return counts.map((c) => `${c.label} ${c.total}${c.male || c.female ? ` (남 ${c.male} · 여 ${c.female})` : ""}`).join(" · ");
}

/** 60대 이상 표본이 적으면 안내 글자, 아니면 null. 태그가 달린 계정이 하나도 없으면 알릴 근거가 없어 null */
export function thinNote(counts: readonly LayerCount[], rows: readonly RadarRow[]): string | null {
  if (!rows.some((r) => r.ages.length > 0)) return null;
  const old = counts.find((c) => c.age === "60plus");
  return old && old.total < THIN_MIN ? THIN_NOTE : null;
}

// ---- 고치기 폼

export interface MetaDraft { kind: RadarKind; ages: AgeBand[]; gender: AudienceGender | ""; topic: string }

export function draftOf(row: RadarRow): MetaDraft {
  const g = row.genders.includes("all") || (row.genders.includes("male") && row.genders.includes("female")) ? "all" : row.genders[0] ?? "";
  return { kind: row.kind, ages: AGE_ORDER.filter((a) => row.ages.includes(a)), gender: g, topic: row.topic ?? "" };
}

export function toggleAge(ages: readonly AgeBand[], age: AgeBand): AgeBand[] {
  const set = new Set(ages);
  if (set.has(age)) set.delete(age); else set.add(age);
  return AGE_ORDER.filter((a) => set.has(a));
}

/** 서버로 보낼 값. 주제는 앞뒤 공백을 떼고 20자까지, 비면 null */
export function draftBody(account: string, d: MetaDraft) {
  const topic = d.topic.trim().slice(0, TOPIC_MAX);
  return { account, kind: d.kind, ages: AGE_ORDER.filter((a) => d.ages.includes(a)), genders: d.gender ? [d.gender] : [], topic: topic || null };
}

// ---- 조회 천만 이상 영상

/** 조회수 글자: 12,300,000 → "1,230만회", 150,000,000 → "1.5억회", 8,500 → "8,500회" */
export function viewsLabel(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "—";
  if (n >= 100_000_000) return `${(n / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억회`;
  if (n >= 10_000) return `${Math.floor(n / 10_000).toLocaleString("ko-KR")}만회`;
  return `${n.toLocaleString("ko-KR")}회`;
}

/** 기준 숫자 글자: 10,000,000 → "1,000만" */
export const thresholdLabel = (n: number): string => (n >= 10_000 ? `${Math.floor(n / 10_000).toLocaleString("ko-KR")}만` : n.toLocaleString("ko-KR"));

export const platformLabel = (p: MegaViewItem["platform"]): string => (p === "instagram" ? "인스타" : "유튜브");

/** "오늘" · "3일 전" (모르면 null). now는 시험용 */
export function daysAgoLabel(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const ms = now - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const d = Math.floor(ms / 86_400_000);
  return d < 1 ? "오늘" : `${d}일 전`;
}

/** 영상 줄의 층 칩 글자들 (종류 · 나이대 · 성별 · 주제). 없는 것은 뺀다 */
export function layerChips(x: {
  account_kind?: RadarKind | null; audience_ages?: readonly AgeBand[] | null; audience_genders?: readonly AudienceGender[] | null; topic?: string | null;
}): string[] {
  return [
    ...(x.account_kind ? [KIND_LABEL[x.account_kind]] : []),
    ...ageLabels(x.audience_ages), ...genderLabels(x.audience_genders),
    ...(x.topic?.trim() ? [x.topic.trim()] : []),
  ];
}

/** 조회 천만 이상 칸의 안내 한 줄 (영상이 있으면 null) */
export function megaNote(m: MegaViews): string | null {
  if (m.status === "unknown") return m.reason ?? "조회 천만 이상 영상을 모르는 상태예요.";
  if (m.items.length > 0) return null;
  return `최근 ${m.days}일 안에 조회 ${thresholdLabel(m.instagram_min)}회 이상 영상이 없어요`;
}

/** 서버가 조회수 내림차순으로 주지만 안전하게 한 번 더 정렬해 돌려준다 (원본은 건드리지 않는다) */
export const megaSorted = (items: readonly MegaViewItem[]): MegaViewItem[] => [...items].sort((a, b) => b.views - a.views);
