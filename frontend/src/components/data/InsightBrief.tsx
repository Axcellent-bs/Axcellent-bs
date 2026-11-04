import { useEffect, useState, type ReactNode } from "react";
import { Chip, Empty, TONE, Waiting } from "@/components/finance/bits";
import { okEvidence, type SkillState } from "@/components/data/useSkill";
import { Sources, when } from "@/components/data/Sources";
import { DATA_SERVER, dataAi, kpiLive, type AgeTopic, type TrendAgeOutput, type TrendInsight, type TrendInsightBriefOutput } from "@/lib/data";

// 트렌드 2단계(인사이트): 기획팀이 가져갈 인사이트 브리프. trend_insight_brief(계약 TrendInsightBriefOutput) 응답 그대로.
// - 카드마다: 한 줄 인사이트 · 카테고리 · 광고 재료 가능 여부(초록 가능 / 노랑 실존 이름·상표 주의 / 회색 아님·판단 못 함 + 서버 이유)
//   · 타깃 연령대 + 연령대별 배수 막대 · 데이터 근거(접힘) · 연결 상품(없으면 서버 이유) · 잘 맞는 숏츠 형식 + 표본 수 · 한계
//   · 참고용 예시(접힘) · insight_id(작게, 복사 버튼: 기획팀이 제안에 적는 값)
// - 연령대별 막대: 브리프는 1등 연령대만 준다. 막대는 trend_age(계약 TrendAgeOutput)를 같은 주제로 불러 그린다.
//   서버가 주제별 하루 캐시라 브리프가 이미 본 주제(타깃 연령대가 있는 주제)만 보낸다 = 네이버를 다시 부르지 않는다.
//   막대 길이는 그 주제 연령대 배수 중 가장 큰 값에 대한 비율(그리기용)
// - 응답에 없는 칸(null·빈 목록)은 그리지 않는다. 이유가 오면 이유만

const NO_REASON = "서버가 이유를 주지 않았어요";
const num = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const times = (v: number) => `평소의 ${num(v, 2)}배`;
const norm = (s: string) => s.trim().toLowerCase();

function material(i: TrendInsight): { label: string; tone: string } {
  if (i.ad_material === true && i.real_entity) return { label: "광고 재료 가능 · 이름 주의", tone: TONE.warn };
  if (i.ad_material === true) return { label: "광고 재료 가능", tone: TONE.ok };
  if (i.ad_material === false) return { label: "광고 재료 아님", tone: TONE.muted };
  return { label: "광고 재료 판단 못 함", tone: TONE.muted };
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
      <p className="text-xs font-semibold text-ink-500">{label}</p>
      <div className="min-w-0 text-sm text-ink-700">{children}</div>
    </div>
  );
}

function CopyId({ id }: { id: string }) {
  const [done, setDone] = useState<"idle" | "ok" | "failed">("idle");
  const copy = () => {
    if (!navigator.clipboard) { setDone("failed"); return; }
    navigator.clipboard.writeText(id).then(() => setDone("ok"), () => setDone("failed"));
  };
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-500">
      <code className="rounded bg-ink-50 px-1.5 py-0.5 font-mono">{id}</code>
      <button type="button" className="rounded px-1.5 py-0.5 font-semibold text-brand-700 ring-1 ring-ink-100 hover:bg-brand-50" onClick={copy}
        aria-label={`인사이트 번호 ${id} 복사`}>
        {done === "ok" ? "복사됨" : "복사"}
      </button>
      {done === "failed" && <span className="text-red-700">복사하지 못했어요</span>}
    </span>
  );
}

function AgeBars({ topic, note }: { topic: AgeTopic; note: string }) {
  const bands = topic.bands.filter((b) => typeof b.ratio === "number");
  const max = Math.max(0, ...bands.map((b) => b.ratio ?? 0));
  if (bands.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5">
      <ul className="space-y-1">
        {bands.map((b) => (
          <li key={b.band} className="grid grid-cols-[3.5rem_minmax(0,1fr)_5.5rem] items-center gap-2 text-xs">
            <span className={b.band === topic.top_band ? "font-semibold text-ink-900" : "text-ink-700"}>{b.band}</span>
            <span className="h-2 rounded-full bg-ink-100" aria-hidden>
              <span className={`block h-2 rounded-full ${b.status === "rising" ? "bg-brand-500" : "bg-ink-300"}`}
                style={{ width: `${max > 0 ? Math.max(2, ((b.ratio ?? 0) / max) * 100) : 0}%` }} />
            </span>
            <span className="text-right tabular-nums text-ink-700">{num(b.ratio ?? 0, 2)}배</span>
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-ink-500">연령대끼리 양 비교 아님{note ? ` · ${note}` : ""}</p>
    </div>
  );
}

function Evidence({ e }: { e: TrendInsight["evidence"] }) {
  const rows: { source: string; body: ReactNode }[] = [];
  if (e.youtube_videos !== null) {
    const parts = [`인기 영상 ${num(e.youtube_videos)}개`];
    if (e.youtube_views_per_hour !== null) parts.push(`시간당 조회 ${num(e.youtube_views_per_hour)}회`);
    if (e.youtube_views_per_subscriber !== null) parts.push(`구독자 대비 최대 ${num(e.youtube_views_per_subscriber, 2)}배`);
    if (e.youtube_at) parts.push(`${when(e.youtube_at)} 기준`);
    rows.push({ source: "YouTube", body: parts.join(" · ") });
  }
  if (e.google_rank !== null) {
    rows.push({
      source: "Google 인기 검색어",
      body: (
        <>
          {num(e.google_rank)}위{e.google_at ? ` · ${when(e.google_at)} 기준` : ""}
          {e.google_news.length > 0 && (
            <ul className="mt-0.5 space-y-0.5">
              {e.google_news.map((n, k) => (
                <li key={`${n.url ?? n.title}-${k}`} className="truncate">
                  {n.url ? <a href={n.url} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline">{n.title}</a> : n.title}
                  {n.source && <span className="text-ink-500"> · {n.source}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      ),
    });
  }
  if (e.naver_ratio !== null) {
    const parts = [times(e.naver_ratio)];
    if (e.naver_status === "rising") parts.push("급상승");
    if (e.naver_rising_started) parts.push(`${e.naver_rising_started}부터 상승`);
    if (e.naver_until) parts.push(`${e.naver_until}까지`);
    rows.push({ source: "네이버 검색", body: parts.join(" · ") });
  }
  const videoLinks = e.links.filter((l) => l.includes("youtube.com/watch"));
  return (
    <div className="space-y-1.5 text-xs text-ink-700">
      {rows.length > 0 && (
        <ul className="space-y-1">
          {rows.map((r) => <li key={r.source} className="min-w-0"><span className="font-semibold text-ink-900">{r.source}</span> · {r.body}</li>)}
        </ul>
      )}
      {videoLinks.length > 0 && (
        <p className="flex flex-wrap gap-x-2">
          {videoLinks.map((l, k) => <a key={l} href={l} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline">▶ 근거 영상 {k + 1}</a>)}
        </p>
      )}
      {e.missing.length > 0 && <p className="text-ink-500">없는 출처 · {e.missing.join(" · ")}</p>}
    </div>
  );
}

function InsightCard({ i, age, note }: { i: TrendInsight; age: AgeTopic | null; note: string | null }) {
  const m = material(i);
  return (
    <li className="card space-y-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink-900 text-xs font-bold text-white">{i.rank}</span>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-bold text-ink-900">{i.topic}</h3>
          {i.aliases.length > 0 && <p className="text-xs text-ink-500">같은 주제 · {i.aliases.join(", ")}</p>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {i.category && i.category !== "unknown" && <Chip label={i.category} tone="bg-ink-100 text-ink-700" />}
          <Chip label={m.label} tone={m.tone} />
        </div>
      </div>
      {i.line && (
        <p className="rounded-xl bg-brand-50 px-3 py-2 text-sm font-semibold leading-relaxed text-brand-900">
          {i.line}
          {i.line_status === "fixed" && i.line_reason && <span className="mt-0.5 block text-xs font-normal text-ink-500">고정 문장 · {i.line_reason}</span>}
        </p>
      )}
      <div className="space-y-2.5">
        {(i.ad_material_reason || (i.real_entity && i.entity_names.length > 0)) && (
          <Row label="광고 재료">
            {[i.ad_material_reason, i.real_entity && i.entity_names.length ? `이름 · ${i.entity_names.join(", ")}` : null].filter(Boolean).join(" · ")}
          </Row>
        )}
        <Row label="타깃 연령대">
          {i.target_age ? (
            <>
              <span className="font-semibold text-ink-900">{i.target_age}</span>
              {i.target_age_ratio !== null && <span> · {times(i.target_age_ratio)}</span>}
              {i.target_age_status === "rising" && <span> · 급상승</span>}
              {age && <AgeBars topic={age} note={note && !i.limits.includes(note) ? note : ""} />}
            </>
          ) : <span className="text-ink-500">{i.target_age_reason ?? NO_REASON}</span>}
        </Row>
        <Row label="연결 상품">
          {i.products.length > 0 ? (
            <ul className="space-y-1">
              {i.products.map((p) => (
                <li key={p.product_id}>
                  <span className="font-semibold text-ink-900">{p.name}</span>
                  {p.category_name && <span className="text-ink-500"> · {p.category_name}</span>}
                  <span className="block text-xs text-ink-500">{[p.reason, p.season].filter(Boolean).join(" · ")}</span>
                </li>
              ))}
            </ul>
          ) : <span className="text-ink-500">{i.products_reason ?? NO_REASON}</span>}
        </Row>
        <Row label="잘 맞는 숏츠 형식">
          {i.shorts_format ? (
            <><span className="font-semibold text-ink-900">{i.shorts_format}</span>{i.shorts_format_samples !== null && <span className="text-ink-500"> · 표본 {num(i.shorts_format_samples)}개</span>}</>
          ) : <span className="text-ink-500">{i.shorts_format_reason ?? NO_REASON}</span>}
        </Row>
        {i.limits.length > 0 && (
          <Row label="한계">
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-500">{i.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </Row>
        )}
      </div>
      <details className="rounded-xl bg-ink-50 px-3 py-2">
        <summary className="cursor-pointer text-xs font-semibold text-ink-700">데이터 근거 보기</summary>
        <div className="mt-2"><Evidence e={i.evidence} /></div>
      </details>
      {i.example_ideas.length > 0 && (
        <details className="rounded-xl px-3 py-2 ring-1 ring-ink-100">
          <summary className="cursor-pointer text-xs font-semibold text-ink-700">참고용 예시 {i.example_ideas.length}개</summary>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-sm text-ink-700">{i.example_ideas.map((x) => <li key={x}>{x}</li>)}</ul>
        </details>
      )}
      <div className="border-t border-ink-100 pt-2"><CopyId id={i.insight_id} /></div>
    </li>
  );
}

/** 브리프가 이미 연령대를 본 주제(타깃 연령대가 있는 주제)만 trend_age로 다시 받아 연령대별 막대를 그린다 (서버 캐시) */
function useAgeBands(out: TrendInsightBriefOutput | null): TrendAgeOutput | null {
  const [age, setAge] = useState<TrendAgeOutput | null>(null);
  const keys = (out?.insights ?? []).filter((i) => i.target_age).map((i) => i.topic);
  const key = keys.join("\n");
  useEffect(() => {
    if (!kpiLive || !key) return;
    let live = true;
    dataAi.trendAge(key.split("\n")).then((r) => live && setAge(r.output)).catch(() => live && setAge(null));
    return () => { live = false; };
  }, [key]);
  return age;
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받는다. examples = 참고용 예시(기존 기획안 카드), examplesCount = 그 개수 */
export function InsightBrief({ state, examples, examplesCount }: {
  state: SkillState<TrendInsightBriefOutput>; examples: ReactNode; examplesCount: number | null;
}) {
  const out = state.kind === "ok" ? state.out : null;
  const age = useAgeBands(out);
  if (!kpiLive) return null;
  const ageOf = (i: TrendInsight) => age?.items.find((a) => a.status === "known" && norm(a.keyword) === norm(i.topic)) ?? null;
  const meta = out ? [out.day ? `${out.day} 브리프` : null, out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null, out.cached ? "오늘 만든 브리프" : null].filter(Boolean).join(" · ") : null;
  return (
    <section id="data-trend-insight" aria-labelledby="data-trend-insight-title" className="space-y-4">
      <div className="card space-y-2 p-4 sm:p-5">
        <h2 id="data-trend-insight-title" className="text-lg font-bold text-ink-900">인사이트 브리프</h2>
        <p className="text-xs text-ink-500">{meta || "기획팀이 이 인사이트를 가져가 제안을 만들어요."}</p>
        {out?.limits && out.limits.length > 0 && (
          <details className="text-xs text-ink-500">
            <summary className="cursor-pointer font-semibold text-ink-700">브리프 한계 {out.limits.length}개</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{out.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        )}
        <Sources evidence={okEvidence(state)} label="근거 · 브리프가 불러온 곳" />
      </div>
      {state.kind === "loading" ? <Waiting what="인사이트를 묶는 중…" />
        : state.kind === "failed" ? <Empty>인사이트를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (out.status === "unknown" || out.insights.length === 0)
          ? <Empty>{out.reason ?? "오늘 인사이트가 없어요."}</Empty>
          : out && <ul className="space-y-4">{out.insights.map((i) => <InsightCard key={i.insight_id} i={i} age={ageOf(i)} note={age?.note ?? null} />)}</ul>}
      <details className="card p-4">
        <summary className="cursor-pointer text-sm font-semibold text-ink-900">
          참고용 예시{examplesCount !== null ? ` ${examplesCount}개` : ""} <span className="font-normal text-ink-500">· 숏츠 광고 예시 아이디어</span>
        </summary>
        <div className="mt-4">{examples}</div>
      </details>
    </section>
  );
}
