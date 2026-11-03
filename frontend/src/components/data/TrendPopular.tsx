import { useEffect, useState } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import { DATA_SERVER, dataAi, kpiLive, type PopularTopic, type PopularVideo, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계 맨 위 "지금 인기": 데이터 AI(8106) trend_popular 출력을 그대로 보여 준다 (계약 TrendPopularOutput, #411).
// - 급상승 판정과 별개인 지금 인기 순위다. 서버 카테고리별 보기(by_category, #431)를 서버가 준 칸 순서 그대로 그린다
//   (회사 설정 [popular] categories, 예: 게임·뉴스·사건·연예·스포츠·IT, 칸마다 1~5위). 모자란 칸은 서버 이유(shortage_reason)
// - 실존 인물·팀·리그·상표 표시(real_entity·entity_names)가 있으면 작은 칩
// - by_category가 없는 예전 응답이면 items를 주제 분류별로 묶기만 한다
// - 근거는 쉬운 말로: "YouTube 인기 영상 N개", "인기 검색어 N위". 숫자는 서버 값만, 없으면(null) 그 줄을 빼고 0으로 채우지 않는다
// - "지금 급등 중" 배지는 서버가 rising_now = true를 줄 때만
// - 출처(YouTube·조회 속도·인기 검색어)를 못 읽었으면 서버 이유를 그대로 보여 준다
// - 네이버를 부르지 않는다. 트렌드 탭을 열 때 한 번 + "다시 보기"

const num = (v: number | null | undefined, digits = 0) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);

/** 분류 이름. 서버가 분류하지 못했으면(unknown·없음) "분류 못 함" */
const categoryOf = (t: PopularTopic) => (t.topic_category && t.topic_category !== "unknown" ? t.topic_category : "분류 못 함");

/** 근거 한 줄 (서버 값이 있는 것만) */
function evidence(t: PopularTopic): string[] {
  const out: string[] = [];
  if (t.youtube_videos != null) out.push(`YouTube 인기 영상 ${num(t.youtube_videos)}개`);
  if (t.trending_rank != null) out.push(`인기 검색어 ${t.trending_rank}위`);
  if (t.views_per_hour != null) out.push(`지금 시간당 조회 +${num(t.views_per_hour)}`);
  return out;
}

function Row({ t, rank }: { t: PopularTopic; rank: number | null }) {
  const lines = evidence(t);
  const names = t.entity_names ?? [];
  return (
    <li className="flex flex-wrap items-start gap-3 py-3">
      <span className="w-6 text-sm font-semibold text-ink-500">{rank ?? "-"}</span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-ink-900">
          {t.keyword}
          {t.rising_now === true && <Chip label="지금 급등 중" tone={TONE.bad} />}
          {t.real_entity === true && (
            <span className="chip bg-amber-50 text-[10px] text-amber-700" title="실존 인물·팀·리그·상표가 든 주제예요">
              {names.length ? `실존 이름 · ${names.join(", ")}` : "실존 이름"}
            </span>
          )}
        </p>
        {t.aliases.length > 0 && <p className="text-[11px] text-ink-500">함께 센 이름: {t.aliases.join(", ")}</p>}
        <p className="text-xs text-ink-700">{lines.length ? lines.join(" · ") : "근거 숫자를 받지 못했어요"}</p>
      </div>
    </li>
  );
}

/** 출처 줄: 시각, 또는 못 읽은 이유 그대로 */
function sourceLines(out: TrendPopularOutput): { text: string; warn: boolean }[] {
  return [
    out.youtube_status === "known"
      ? { text: `YouTube 인기 스냅샷 ${when(out.snapshot_at) ?? "-"}`, warn: false }
      : { text: `YouTube: ${out.youtube_reason ?? "서버가 이유를 주지 않았어요"}`, warn: true },
    out.trending_status === "known"
      ? { text: `인기 검색어 ${when(out.trending_at) ?? "-"}`, warn: false }
      : { text: `인기 검색어: ${out.trending_reason ?? "서버가 이유를 주지 않았어요"}`, warn: true },
    ...(out.velocity_status === "unknown" ? [{ text: `조회 속도: ${out.velocity_reason ?? "서버가 이유를 주지 않았어요"}`, warn: true }] : []),
  ];
}

/** 화면 칸 하나. legacy = 예전 응답을 화면이 주제 분류별로 묶은 칸(순위는 전체 순위) */
type Cell = { category: string; items: PopularTopic[]; shortage_reason: string | null; top_videos?: PopularVideo[]; top_videos_reason?: string | null; legacy?: boolean };

type State = { kind: "loading" } | { kind: "ok"; out: TrendPopularOutput } | { kind: "failed"; reason: string };

export function TrendPopular() {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.trendPopular()
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);
  if (!kpiLive) return null;

  const out = state.kind === "ok" ? state.out : null;
  // 칸: 서버 카테고리별 보기 순서 그대로. 예전 응답(by_category 없음)이면 items를 주제 분류별로 묶는다(점수 순으로 처음 나온 순서)
  const cells: Cell[] = out?.by_category?.length ? out.by_category : (() => {
    const groups = new Map<string, PopularTopic[]>();
    for (const t of out?.items ?? []) groups.set(categoryOf(t), [...(groups.get(categoryOf(t)) ?? []), t]);
    return [...groups.entries()].map(([category, items]) => ({ category, items, shortage_reason: null, legacy: true }));
  })();
  const empty = cells.every((c) => c.items.length === 0) && !out?.by_category?.length;

  return (
    <Panel id="data-trend-popular" title="지금 인기"
      hint={out ? (
        <span className="flex flex-wrap gap-x-3">
          {sourceLines(out).map((l) => <span key={l.text} className={l.warn ? "text-amber-700" : undefined}>{l.text}</span>)}
        </span>
      ) : "급상승 판정과 별개로, 지금 많이 보이는 주제예요."}
      tools={<button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 보기</button>}>
      {state.kind === "loading" ? <Waiting what="지금 인기를 받는 중…" />
        : state.kind === "failed" ? <Empty>지금 인기를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : !out || empty ? <Empty>{out?.empty_reason ?? "지금 인기 주제가 없어요."}</Empty> : (
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              {cells.map((c) => (
                <section key={c.category} aria-label={c.category} className="rounded-xl bg-ink-50 px-3 py-2">
                  <p className="panel-title">{c.category === "unknown" ? "분류 못 함" : c.category}</p>
                  {c.items.length > 0 && (
                    <ol className="divide-y divide-ink-100">
                      {c.items.map((t) => <Row key={t.keyword} t={t} rank={c.legacy ? t.rank : t.category_rank ?? null} />)}
                    </ol>
                  )}
                  {c.shortage_reason && <p className="py-2 text-xs text-amber-700">{c.shortage_reason}</p>}
                  {c.items.length === 0 && !c.shortage_reason && <p className="py-2 text-xs text-ink-500">이 칸에 들어온 주제가 없어요. 서버가 이유를 주지 않았어요.</p>}
                  {(c.top_videos?.length ?? 0) > 0 && (
                    <div className="mt-1 border-t border-ink-100 pt-2">
                      <p className="flex flex-wrap items-center gap-2 text-xs font-semibold text-ink-700">
                        최근 조회수 높은 영상 <span className="chip">지금 인기 아님</span>
                      </p>
                      <ol className="divide-y divide-ink-100">
                        {(c.top_videos ?? []).map((v) => (
                          <li key={v.video_id} className="flex items-baseline gap-2 py-1.5 text-sm">
                            <a className="min-w-0 flex-1 truncate text-ink-900 hover:underline" href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`} target="_blank" rel="noreferrer">
                              {v.title ?? "제목 없음"}
                            </a>
                            <span className="shrink-0 text-xs text-ink-500">
                              {v.channel_title ?? "채널 모름"}{v.views != null ? ` · 조회 ${v.views.toLocaleString("ko-KR")}회` : ""}
                            </span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                  {c.top_videos_reason && <p className="py-1 text-xs text-ink-500">{c.top_videos_reason}</p>}
                </section>
              ))}
            </div>
            {out.summary && <p className="text-xs text-ink-500">{out.summary}</p>}
          </div>
        )}
    </Panel>
  );
}
