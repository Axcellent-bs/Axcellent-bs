import type { ReactNode } from "react";
import type { Evidence } from "@/lib/data";

// 트렌드 화면의 근거 표시. 값은 모두 서버 응답(evidence.queries·collected_at, 영상 id 등) 그대로다.
// - Sources: 응답 하나가 부른 외부 API·저장소 목록(evidence.queries)을 출처 이름과 원래 줄로 보여 준다(접힘)
// - VideoLinks: YouTube 영상 id → 영상 주소 링크. 제목을 알면 제목, 모르면 "영상 N"
// - Basis: 카드·줄 아래 "근거" 한 덩어리

const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const when = (iso: string | null | undefined) => (iso ? TIME.format(new Date(iso)) : null);

/** 서버 query 줄의 앞부분 → 출처 이름 (모르는 줄은 원래 줄만 보여 준다) */
const SOURCE_NAMES: [RegExp, string][] = [
  [/^youtube snapshots? velocity/i, "YouTube 조회수 스냅샷 비교 (시간당 증가)"],
  [/^youtube snapshot popular/i, "YouTube Data API 인기 차트 (videos.list chart=mostPopular, 한국)"],
  [/^youtube search|search\.list/i, "YouTube Data API 검색 (search.list)"],
  [/^youtube/i, "YouTube Data API"],
  [/^naver search-trend/i, "네이버 데이터랩 검색어 트렌드 (상대값, 최댓값 = 100)"],
  [/^naver/i, "네이버 데이터랩"],
  [/^trending snapshot/i, "Google 인기 검색어 RSS (trends.google.com/trending/rss)"],
  [/^shop-api/i, "캣북스 상품 목록 (shop-api)"],
  [/^llm/i, "LLM (문장만 씀, 숫자는 코드 검사)"],
  [/cache/i, "오늘 만든 결과 다시 씀 (하루 캐시)"],
];

export function sourceName(query: string): string | null {
  return SOURCE_NAMES.find(([re]) => re.test(query.trim()))?.[1] ?? null;
}

/** 응답 하나의 출처 목록. 같은 출처는 묶고 원래 줄은 접어 둔다 */
export function Sources({ evidence, label = "근거 · 불러온 곳" }: { evidence: Evidence | null | undefined; label?: string }) {
  const queries = evidence?.queries ?? [];
  if (!evidence) return null;
  const groups = new Map<string, string[]>();
  for (const q of queries) {
    const name = sourceName(q) ?? "기타";
    groups.set(name, [...(groups.get(name) ?? []), q]);
  }
  const at = when(evidence.collected_at);
  return (
    <details className="text-xs text-ink-700">
      <summary className="cursor-pointer font-semibold text-ink-900">
        {label} · {groups.size ? [...groups.keys()].filter((n) => n !== "기타").join(" · ") || "기타" : "서버가 출처를 주지 않았어요"}
        {at && <span className="ml-1 font-normal text-ink-500">({at} 응답)</span>}
      </summary>
      {groups.size > 0 && (
        <ul className="mt-2 space-y-1.5 rounded-xl bg-ink-50 px-3 py-2">
          {[...groups.entries()].map(([name, lines]) => (
            <li key={name}>
              <p className="font-semibold text-ink-900">{name} <span className="font-normal text-ink-500">· 호출 {lines.length}번</span></p>
              <ul className="mt-0.5 space-y-0.5 break-all font-mono text-[11px] text-ink-500">
                {lines.slice(0, 8).map((l, i) => <li key={`${l}-${i}`}>{l}</li>)}
                {lines.length > 8 && <li>… 그 밖에 {lines.length - 8}줄</li>}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

export const videoUrl = (id: string) => `https://www.youtube.com/watch?v=${encodeURIComponent(id)}`;

/** YouTube 영상 링크 목록. titles: 영상 id → 제목(아는 것만) */
export function VideoLinks({ ids, titles, max = 3 }: { ids: string[]; titles?: Map<string, string | null>; max?: number }) {
  if (ids.length === 0) return null;
  return (
    <span className="inline-flex min-w-0 flex-wrap gap-x-2 gap-y-0.5">
      {ids.slice(0, max).map((id, i) => (
        <a key={id} href={videoUrl(id)} target="_blank" rel="noreferrer noopener" className="max-w-[16rem] truncate text-brand-700 underline-offset-2 hover:underline">
          ▶ {titles?.get(id) || `영상 ${i + 1}`}
        </a>
      ))}
      {ids.length > max && <span className="text-ink-500">외 {ids.length - max}개</span>}
    </span>
  );
}

/** "근거" 한 덩어리: 줄마다 출처 이름 + 내용 */
export function Basis({ rows }: { rows: { source: string; body: ReactNode }[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-700">
      <p className="font-semibold text-ink-500">근거</p>
      <ul className="mt-0.5 space-y-1">
        {rows.map((r, i) => (
          <li key={`${r.source}-${i}`} className="min-w-0">
            <span className="font-semibold text-ink-900">{r.source}</span> · {r.body}
          </li>
        ))}
      </ul>
    </div>
  );
}
