import { useState, type ReactNode } from "react";
import { TONE } from "@/components/finance/bits";
import type { CautionType, DataFact, ShortsFit, VideoLink } from "@/lib/data";

// 2단계 인사이트 v2 카드 틀 (v2 시안 ② 보드). 인사이트 카드와 기회 가설 카드가 같이 쓴다.
// 왼쪽: 판정 칩 하나 → 제목 → 한 문장(서버 line) → 숫자 타일(서버 값이 있는 것만) → 연결 상품 → "근거 전체 보기" 접힘 하나
// 오른쪽: 근거 숏츠 썸네일 최대 3개. 서버 thumbnail_url 이미지(세로 비율, cover). 값이 없거나 깨지면 회색 자리.
//   누르면 서버 video_url(없으면 근거 주소) 새 탭
// 문단 글은 카드당 한 문장(line)만. 나머지 이유·한계·예시는 접힘 안
// 판정 칩·숫자 타일(verdictOf·shortsTiles)은 서버 칸 그대로: shorts_fit · target_views_reference · top_age_band (#678). 화면에서 고르지 않는다

export type Verdict = { label: string; tone: string; title?: string | null };
export type Tile = { label: string; value: string; note?: string | null };
/** 근거 숏츠 하나: 누르면 갈 주소, 썸네일 주소(없으면 null), 서버 광고 재료 칸(VideoLink에 있을 때만) */
export type Thumb = { url: string; thumb: string | null; caution?: CautionFields };

const fmt = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });

/** 판정 칩: 숏츠 적합도 high → "숏츠로 해볼 만함", 그 밖(uncertain·low, 표본 부족 unknown) → "아직 불확실". 칸이 없는 예전 응답은 칩 없음 */
export function verdictOf(fit: ShortsFit | null | undefined, status?: "known" | "unknown" | null, reason?: string | null): Verdict | null {
  if (fit) return fit.level === "high" ? { label: "숏츠로 해볼 만함", tone: TONE.ok, title: fit.reason } : { label: "아직 불확실", tone: TONE.muted, title: fit.reason };
  if (status === "unknown") return { label: "아직 불확실", tone: TONE.muted, title: reason ?? null };
  return null;
}

/** 숫자 타일 3개(서버 값이 있는 것만): 숏츠 반응(shorts_fit.ratio·samples) · 가장 오른 연령(top_age_band·배수) · 목표 참고값(target_views_reference) */
export function shortsTiles(x: {
  shorts_fit?: ShortsFit | null; target_views_reference?: number | null; target_reference_basis?: string | null;
  top_age_band?: string | null; top_age_band_ratio?: number | null;
}): Tile[] {
  const tiles: Tile[] = [];
  const fit = x.shorts_fit;
  if (fit && typeof fit.ratio === "number") {
    tiles.push({ label: "숏츠 반응", value: `${fmt(fit.ratio, 2)}배`, note: [fit.category, fit.samples ? `표본 ${fmt(fit.samples)}개` : null].filter(Boolean).join(" · ") || null });
  }
  if (x.top_age_band) {
    tiles.push({ label: "가장 오른 연령", value: x.top_age_band, note: typeof x.top_age_band_ratio === "number" ? `검색 평소의 ${fmt(x.top_age_band_ratio, 2)}배` : null });
  }
  if (typeof x.target_views_reference === "number") {
    tiles.push({ label: "목표 참고값", value: `${COMPACT.format(x.target_views_reference)}회`, note: x.target_reference_basis ?? null });
  }
  return tiles;
}

const YOUTUBE = /^https:\/\/(www\.|m\.)?(youtube\.com\/(watch|shorts)|youtu\.be\/)/;
const YOUTUBE_ID = /(?:[?&]v=|\/shorts\/|youtu\.be\/)([A-Za-z0-9_-]{6,})/;
const idOf = (url: string) => YOUTUBE_ID.exec(url)?.[1] ?? url;

/** 근거 숏츠 최대 3개. 서버 video_links(주소·썸네일)를 먼저, 이어서 근거 주소(evidence.links·facts[].link) 중 YouTube 영상.
 *  같은 영상(id)은 한 번만이고, 썸네일이 있는 쪽을 쓴다 */
export function shortsThumbs(videoLinks: VideoLink[] | undefined, links: (string | null | undefined)[]): Thumb[] {
  const out: Thumb[] = [];
  const seen = new Set<string>();
  const push = (url: string | null | undefined, thumb: string | null, id?: string, caution?: CautionFields) => {
    if (!url || !YOUTUBE.test(url) || out.length === 3) return;
    const key = id ?? idOf(url);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ url, thumb, caution });
  };
  for (const v of videoLinks ?? []) push(v.video_url, v.thumbnail_url, v.video_id, v);
  for (const l of links) push(l, null);
  return out;
}

/** 썸네일(기본 세로 9:16, wide면 가로 16:9). 서버 thumbnail_url이 있으면 이미지(object-fit: cover), 없거나 깨지면 회색 자리 그대로 */
export function ThumbImage({ src, alt, className = "", wide = false, children }: {
  src: string | null | undefined; alt: string; className?: string; wide?: boolean; children?: ReactNode;
}) {
  const [broken, setBroken] = useState<string | null>(null);
  const ok = !!src && /^https:\/\//.test(src) && broken !== src;
  return (
    <span className={`relative block ${wide ? "aspect-video" : "aspect-[9/16]"} overflow-hidden rounded-xl bg-ink-100 ${className}`}>
      {ok && <img src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBroken(src)}
        className="absolute inset-0 h-full w-full object-cover" />}
      {children}
    </span>
  );
}

const CAUTION_LABEL: Record<CautionType, string> = { real_person: "실존 인물", trademark: "상표", music: "음원" };
type CautionFields = { ad_usable?: boolean | null; caution_types?: CautionType[]; caution_reason?: string | null };

/** 서버 광고 재료 칸이 있는지 (예전 응답·칸 없는 영상은 false) */
export const hasCaution = (v: CautionFields) => typeof v.ad_usable === "boolean" || (v.caution_types?.length ?? 0) > 0;

/** 영상 칩 하나 (서버 ad_usable·caution_types 그대로, #678). 확인할 것이 있으면 "실존 인물·음원 주의"처럼 노랑,
 *  없고 ad_usable true면 "광고 재료 가능" 초록. 모르면(null) 그리지 않는다. 마우스를 올리면 서버 이유 */
export function CautionChip({ v, className = "text-xs" }: { v: CautionFields; className?: string }) {
  const types = v.caution_types ?? [];
  if (types.length > 0) {
    return <span className={`chip ${className} ${TONE.warn}`} title={v.caution_reason ?? undefined}>{types.map((t) => CAUTION_LABEL[t]).join("·")} 주의</span>;
  }
  if (v.ad_usable === true) return <span className={`chip ${className} ${TONE.ok}`}>광고 재료 가능</span>;
  if (v.ad_usable === false) return <span className={`chip ${className} ${TONE.muted}`} title={v.caution_reason ?? undefined}>광고에 안 씀</span>;
  return null;
}

/** 브리프 facts 중 이 항목(about = insight_id·opportunity_id)이거나 fact_ids가 가리키는 숫자의 근거 주소 */
export function factLinks(facts: DataFact[] | undefined, about: string, ids: string[] | undefined): (string | null)[] {
  const want = new Set(ids ?? []);
  return (facts ?? []).filter((f) => f.about === about || want.has(f.fact_id)).map((f) => f.link);
}

export function ShortsCard({ verdict, kicker, title, line, lineNote, tiles, products, thumbs, fold, highlight }: {
  verdict: Verdict | null;
  kicker?: string | null;
  title: string;
  line: string | null;
  /** 한 문장 옆 작은 표시 (예: 고정 문장) */
  lineNote?: string | null;
  tiles: Tile[];
  products: { id: string; name: string }[];
  thumbs: Thumb[];
  fold: ReactNode;
  highlight?: boolean;
}) {
  return (
    <li className={`card flex flex-wrap gap-5 p-4 sm:p-5 ${highlight ? "ring-2 ring-emerald-200" : ""}`}>
      <div className="flex min-w-0 flex-[999_1_30rem] flex-col gap-3">
        {(verdict || kicker) && (
          <div className="flex flex-wrap items-center gap-2">
            {verdict && <span className={`chip text-sm font-bold ${verdict.tone}`} title={verdict.title ?? undefined}>{verdict.label}</span>}
            {kicker && <span className="text-xs text-ink-500">{kicker}</span>}
          </div>
        )}
        <h3 className="text-xl font-bold text-ink-900">{title}</h3>
        {line && (
          <p className="text-base leading-relaxed text-ink-900">
            {line}{lineNote && <span className="ml-1.5 text-xs text-ink-500">({lineNote})</span>}
          </p>
        )}
        {tiles.length > 0 && (
          <dl className="grid gap-2.5 sm:grid-cols-3">
            {tiles.map((t) => (
              <div key={t.label} className="flex flex-col gap-0.5 rounded-xl bg-ink-50 p-3">
                <dt className="text-xs font-semibold text-ink-500">{t.label}</dt>
                <dd className="text-2xl font-bold tabular-nums text-ink-900">{t.value}</dd>
                {t.note && <dd className="text-xs text-ink-500">{t.note}</dd>}
              </div>
            ))}
          </dl>
        )}
        {products.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-semibold text-ink-500">연결 상품</span>
            {products.map((p) => <span key={p.id} className="chip bg-surface text-sm text-ink-700 ring-1 ring-ink-100">{p.name}</span>)}
          </div>
        )}
        <details className="rounded-xl bg-ink-50 px-3 py-2">
          <summary className="cursor-pointer text-sm font-semibold text-ink-700">근거 전체 보기</summary>
          <div className="mt-2 space-y-3 text-sm text-ink-700">{fold}</div>
        </details>
      </div>
      {thumbs.length > 0 && (
        <div className="flex min-w-0 flex-[1_1_16rem] flex-col gap-2">
          <p className="text-xs font-semibold text-ink-500">근거 숏츠</p>
          <ul className="grid grid-cols-3 gap-2">
            {thumbs.map((t, k) => (
              <li key={t.url}>
                {/* 근거 영상 제목은 응답(VideoLink)에 없어 alt는 순번 */}
                <a href={t.url} target="_blank" rel="noreferrer noopener" aria-label={`근거 숏츠 ${k + 1} · YouTube에서 보기`}
                  className="block rounded-xl transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                  <ThumbImage src={t.thumb} alt={`근거 숏츠 ${k + 1}`} />
                </a>
                {t.caution && <span className="mt-1 block empty:hidden"><CautionChip v={t.caution} className="text-[11px]" /></span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/** 접힘 안 한 묶음 (제목 + 내용) */
export function FoldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
      <p className="text-xs font-semibold text-ink-500">{label}</p>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** 작은 id와 복사 버튼 (기획팀이 제안에 적는 값). 접힘 안에 둔다 */
export function CopyId({ id, label = "인사이트 번호" }: { id: string; label?: string }) {
  const [done, setDone] = useState<"idle" | "ok" | "failed">("idle");
  const copy = () => {
    if (!navigator.clipboard) { setDone("failed"); return; }
    navigator.clipboard.writeText(id).then(() => setDone("ok"), () => setDone("failed"));
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-[11px] text-ink-500">
      <span>{label}</span>
      <code className="rounded bg-surface px-1.5 py-0.5 font-mono">{id}</code>
      <button type="button" className="rounded px-1.5 py-0.5 font-semibold text-brand-700 ring-1 ring-ink-100 hover:bg-brand-50" onClick={copy}
        aria-label={`${label} ${id} 복사`}>
        {done === "ok" ? "복사됨" : "복사"}
      </button>
      {done === "failed" && <span className="text-red-700">복사하지 못했어요</span>}
    </span>
  );
}
