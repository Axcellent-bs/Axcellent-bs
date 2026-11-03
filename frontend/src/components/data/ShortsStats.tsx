import { useEffect, useState } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import { DATA_SERVER, dataAi, kpiLive, type ShortsGroup, type ShortsStatsOutput } from "@/lib/data";

// 트렌드 2단계 기획안 위 "인기 숏츠 형식": 데이터 AI(8106) shorts_stats 출력을 그대로 보여 준다 (계약 ShortsStatsOutput, #437).
// - 형식별·카테고리별 평균 조회수 표와 묶음마다 상위 숏츠 제목(+채널). 숫자는 서버 값만, 없으면 "-"(0 아님)
// - 표본 부족·unknown 묶음은 서버 이유 그대로. 표본 한계 문구(limitation)도 서버 문장 그대로
// - 판정하지 않는다("판정 없음" 칩). 트렌드 탭을 열 때 한 번 + "다시 보기" (YouTube 검색은 서버 하루 캐시)

const num = (v: number | null | undefined) =>
  typeof v === "number" && Number.isFinite(v) ? Math.round(v).toLocaleString("ko-KR") : "-";
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : "-");
const NO_REASON = "서버가 이유를 주지 않았어요";

function GroupTable({ title, groups }: { title: string; groups: ShortsGroup[] }) {
  return (
    <section aria-label={title} className="min-w-0 space-y-2">
      <p className="panel-title">{title}</p>
      {groups.length === 0 ? <p className="text-xs text-ink-500">서버가 준 묶음이 없어요.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[22rem] text-left text-sm">
            <thead className="text-[11px] text-ink-500">
              <tr>
                <th scope="col" className="py-1 pr-2 font-semibold">묶음</th>
                <th scope="col" className="py-1 pr-2 text-right font-semibold">숏츠 수</th>
                <th scope="col" className="py-1 pr-2 text-right font-semibold">평균 조회수</th>
                <th scope="col" className="py-1 text-right font-semibold">평균 시간당 조회</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100 align-top">
              {groups.map((g) => (
                <tr key={g.name}>
                  <td className="py-2 pr-2">
                    <p className="font-semibold text-ink-900">{g.name === "unknown" ? "분류 못 함" : g.name}</p>
                    {g.status === "unknown" && <p className="text-xs text-amber-700">{g.reason ?? NO_REASON}</p>}
                    {g.top.length > 0 && (
                      <ul className="mt-1 space-y-0.5 text-xs text-ink-700">
                        {g.top.map((v) => (
                          <li key={v.video_id} className="truncate">
                            <a className="text-brand-700 underline-offset-2 hover:underline" href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`}
                              target="_blank" rel="noreferrer noopener">{v.title || `영상 ${v.video_id}`}</a>
                            {v.channel_title && <span className="text-ink-500"> · {v.channel_title}</span>}
                            {v.views != null && <span className="text-ink-500"> · 조회 {num(v.views)}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums">{num(g.count)}</td>
                  <td className="py-2 pr-2 text-right tabular-nums">{g.status === "known" ? num(g.avg_views) : "-"}</td>
                  <td className="py-2 text-right tabular-nums">
                    {g.status === "known" && g.avg_views_per_hour != null ? `+${num(g.avg_views_per_hour)}` : "-"}
                    {g.velocity_videos > 0 && <span className="block text-[11px] text-ink-500">잰 숏츠 {num(g.velocity_videos)}개</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** 표본 넓히기 검색 상태: 이번에 검색 / 오늘 저장한 결과 / 검색 안 함(서버 이유) */
function searchLine(out: ShortsStatsOutput): { text: string; warn: boolean } {
  if (out.search_status === "known") return { text: `이번에 YouTube 검색 (할당량 ${num(out.quota_used)})`, warn: false };
  if (out.search_status === "cached") return { text: "오늘 검색해 저장한 결과", warn: false };
  return { text: `검색 안 함: ${out.search_reason ?? NO_REASON}`, warn: true };
}

type State = { kind: "loading" } | { kind: "ok"; out: ShortsStatsOutput } | { kind: "failed"; reason: string };

export function ShortsStats() {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.shortsStats()
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);
  if (!kpiLive) return null;

  const out = state.kind === "ok" ? state.out : null;
  const search = out ? searchLine(out) : null;
  return (
    <Panel id="data-trend-shorts" title="인기 숏츠 형식"
      hint={out?.status === "known" ? (
        <span className="flex flex-wrap gap-x-3">
          <span>스냅샷 {when(out.snapshot_at)}</span>
          <span>{num(out.max_seconds)}초 이하 숏츠 {num(out.shorts)}개 (인기 {num(out.popular_shorts)} · 검색 {num(out.searched_shorts)})</span>
          {out.unknown_length > 0 && <span>길이를 몰라 뺀 영상 {num(out.unknown_length)}개</span>}
          {search && <span className={search.warn ? "text-amber-700" : undefined}>{search.text}</span>}
        </span>
      ) : "카테고리·형식별로 인기 숏츠의 평균 조회수를 봐요."}
      tools={<>
        <Chip label="판정 없음" tone={TONE.muted} />
        <button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 보기</button>
      </>}>
      {state.kind === "loading" ? <Waiting what="숏츠 통계를 받는 중…" />
        : state.kind === "failed" ? <Empty>숏츠 통계를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && out.status === "unknown" ? <Empty>확인할 수 없음 · {out.reason ?? NO_REASON}</Empty>
        : out && (
          <div className="space-y-4">
            {out.summary && <p className="text-sm text-ink-700">{out.summary}</p>}
            <div className="grid gap-5 lg:grid-cols-2">
              <GroupTable title="형식별" groups={out.by_format} />
              <GroupTable title="카테고리별" groups={out.by_category} />
            </div>
            {out.velocity_note && <p className="text-xs text-ink-500">{out.velocity_note}</p>}
            {out.limitation && <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700">표본 한계: {out.limitation}</p>}
          </div>
        )}
    </Panel>
  );
}
