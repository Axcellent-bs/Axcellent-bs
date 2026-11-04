import { useState, type ReactNode } from "react";
import type { DataFact } from "@/lib/data";

// 2단계 인사이트 v2 카드 틀 (v2 시안 ② 보드). 인사이트 카드와 기회 가설 카드가 같이 쓴다.
// 왼쪽: 판정 칩 하나 → 제목 → 한 문장(서버 line) → 숫자 타일(서버 값이 있는 것만) → 연결 상품 → "근거 전체 보기" 접힘 하나
// 오른쪽: 근거 숏츠 썸네일 최대 3개. 썸네일 주소는 계약에 없어 회색 자리이고, 누르면 서버가 준 근거 주소(YouTube)로 간다
// 문단 글은 카드당 한 문장(line)만. 나머지 이유·한계·예시는 접힘 안

export type Verdict = { label: string; tone: string };
export type Tile = { label: string; value: string; note?: string | null };

/** 근거 주소 중 YouTube 영상만, 최대 3개 (서버 evidence.links·facts[].link 순서, 같은 주소는 한 번) */
export function shortsLinks(links: (string | null | undefined)[]): string[] {
  const out: string[] = [];
  for (const l of links) {
    if (!l || out.includes(l)) continue;
    if (/^https:\/\/(www\.)?(youtube\.com\/(watch|shorts)|youtu\.be\/)/.test(l)) out.push(l);
    if (out.length === 3) break;
  }
  return out;
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
  thumbs: string[];
  fold: ReactNode;
  highlight?: boolean;
}) {
  return (
    <li className={`card flex flex-wrap gap-5 p-4 sm:p-5 ${highlight ? "ring-2 ring-emerald-200" : ""}`}>
      <div className="flex min-w-0 flex-[999_1_30rem] flex-col gap-3">
        {(verdict || kicker) && (
          <div className="flex flex-wrap items-center gap-2">
            {verdict && <span className={`chip text-sm font-bold ${verdict.tone}`}>{verdict.label}</span>}
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
            {thumbs.map((url, k) => (
              <li key={url}>
                {/* 썸네일 자리 (계약에 썸네일 주소가 아직 없다) */}
                <a href={url} target="_blank" rel="noreferrer noopener" aria-label={`근거 숏츠 ${k + 1} · YouTube에서 보기`}
                  className="block aspect-[9/16] rounded-xl bg-ink-100 transition hover:bg-ink-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500" />
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
