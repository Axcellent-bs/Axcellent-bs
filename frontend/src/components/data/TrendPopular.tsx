import { useEffect, useState } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import { DATA_SERVER, dataAi, kpiLive, type PopularTopic, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계 맨 위 "지금 인기": 데이터 AI(8106) trend_popular 출력을 그대로 보여 준다 (계약 TrendPopularOutput, #411).
// - 급상승 판정과 별개인 지금 인기 순위다. 순서는 서버 점수 순, 화면은 주제 분류별로 묶기만 한다
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

function Row({ t }: { t: PopularTopic }) {
  const lines = evidence(t);
  return (
    <li className="flex flex-wrap items-start gap-3 py-3">
      <span className="w-6 text-sm font-semibold text-ink-500">{t.rank}</span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-ink-900">
          {t.keyword}
          {t.rising_now === true && <Chip label="지금 급등 중" tone={TONE.bad} />}
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
  // 주제 분류별로 묶는다. 묶음 순서는 서버 점수 순으로 처음 나온 순서
  const groups = new Map<string, PopularTopic[]>();
  for (const t of out?.items ?? []) groups.set(categoryOf(t), [...(groups.get(categoryOf(t)) ?? []), t]);

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
        : !out || out.items.length === 0 ? <Empty>{out?.empty_reason ?? "지금 인기 주제가 없어요."}</Empty> : (
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              {[...groups.entries()].map(([category, items]) => (
                <section key={category} aria-label={category} className="rounded-xl bg-ink-50 px-3 py-2">
                  <p className="panel-title">{category}</p>
                  <ul className="divide-y divide-ink-100">{items.map((t) => <Row key={t.keyword} t={t} />)}</ul>
                </section>
              ))}
            </div>
            {out.summary && <p className="text-xs text-ink-500">{out.summary}</p>}
          </div>
        )}
    </Panel>
  );
}
