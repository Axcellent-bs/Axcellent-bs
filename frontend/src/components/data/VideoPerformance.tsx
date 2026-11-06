import { useEffect, useState } from "react";
import { Empty, Panel, Waiting } from "@/components/finance/bits";
import {
  DATA_SERVER, dataAi, kpiLive,
  type BaselineProgress, type CampaignResultOutput, type CampaignSalesCell,
  type SignalLevel, type VideoCheckpoint, type VideoInsightCard, type VideoPerformanceListOutput,
  type VideoPerformanceVideo, type VideoSignal, type VideoVerdict,
} from "@/lib/data";

// 캠페인 매출 효과 (campaign_result, 재무 묶음 = 연결·매출·알림). 캠페인마다 따로 부른다.
type CampaignState =
  | { id: string; kind: "loading" }
  | { id: string; kind: "ok"; out: CampaignResultOutput }
  | { id: string; kind: "failed"; reason: string };

// 영상 성과 (트렌드&콘텐츠 > 성과): 데이터 AI(8106) video_performance list_all 출력을 그대로 보여 준다.
// - 판정(7일 조회수 ÷ 목표)·신호 등급·성과 요인·개선 방향 문장·예상치는 모두 서버 값이다. 화면은 자릿수와 색만 정한다.
// - 같은 형식 비교는 시간 맞춤 값만 쓴다 (시간 안 맞춘 peer_ratio는 보여 주지 않는다).
// - 집계는 인사이트 묶음만 보여 준다 (형식·카테고리 묶음은 응답에만 있다).
// - 시험 영상은 점선·흐리게, 성과 요인·개선 방향 없이 보여 주고 집계에서 뺀다.
// - 영상이 없으면 빈 상태 문구만. 값을 지어내지 않는다.

type State =
  | { kind: "loading" }
  | { kind: "ok"; out: VideoPerformanceListOutput; at: string | null }
  | { kind: "failed"; reason: string };

const CP_NAME = { "1h": "1시간", "1d": "1일", "3d": "3일", "7d": "7일" } as const;
const VERDICT: Record<VideoVerdict, { label: string; badge: string; text: string; bar: string }> = {
  achieved: { label: "목표 달성", badge: "bg-emerald-50 text-emerald-700", text: "text-emerald-700", bar: "bg-emerald-600" },
  missed: { label: "목표 미달", badge: "bg-red-50 text-red-700", text: "text-red-700", bar: "bg-red-600" },
  measuring: { label: "측정 중", badge: "bg-amber-50 text-amber-700", text: "text-amber-700", bar: "bg-amber-500" },
  excluded: { label: "집계 제외", badge: "bg-ink-100 text-ink-500", text: "text-ink-500", bar: "bg-ink-300" },
};
const LEVEL: Record<SignalLevel, { label: string; text: string }> = {
  strong: { label: "강함", text: "text-emerald-700" },
  normal: { label: "보통", text: "text-amber-700" },
  weak: { label: "약함", text: "text-red-700" },
  unknown: { label: "판단 불가", text: "text-ink-500" },
};
const SIGNAL_NAME = { first: "첫 1시간 (목표 대비)", like: "좋아요율", cmt: "댓글율", grow: "1일 이후 성장" } as const;

const n = (v: number) => v.toLocaleString("ko-KR");
const times = (v: number) => `${v.toFixed(2)}배`;
const when = (iso: string | null) => {
  const t = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(t) ? "알 수 없음" : new Date(t).toLocaleString("ko-KR", { dateStyle: "long", timeStyle: "short" });
};
const nameOf = (v: VideoPerformanceVideo) => v.title || v.video_id || "이름 없는 영상";

/** 아직 값이 없는 시점의 짧은 표시: 놓침 (수집 멈춤) · 측정 중 · N시간 뒤 · 연결 대기 */
function pendingWord(c: VideoCheckpoint): { text: string; tone: string; box: string } {
  const reason = c.reason ?? "";
  if (reason.includes("놓침")) {
    return { text: reason.includes("등록 전") ? "놓침 (등록 전)" : "놓침 (수집 멈춤)", tone: "text-ink-500", box: "bg-ink-50" };
  }
  if (reason.startsWith("측정 중")) {
    const hours = /([\d.]+)시간 뒤/.exec(reason)?.[1];
    return { text: hours ? `측정 중 · ${Math.round(Number(hours))}시간 뒤` : "측정 중", tone: "text-amber-700", box: "bg-amber-50" };
  }
  return { text: reason || "값 없음", tone: "text-ink-500", box: "bg-ink-50" };
}

function Checkpoint({ c }: { c: VideoCheckpoint }) {
  const seven = c.checkpoint === "7d";
  const label = seven ? "7일 · 판정" : CP_NAME[c.checkpoint];
  const known = c.status === "known" && typeof c.views === "number";
  const pending = known ? null : pendingWord(c);
  return (
    <div className={`flex min-w-0 flex-col px-3 py-2 ${seven ? "border-2 border-brand-600" : "border-2 border-transparent"} ${pending?.box ?? ""}`}>
      <span className={`text-xs ${seven ? "font-semibold text-brand-700" : "text-ink-500"}`}>{label}</span>
      {known ? (
        <>
          <b className="text-[15px] text-ink-900">{n(c.views as number)}회</b>
          <span className="text-[11px] text-ink-500">
            {typeof c.peer_age_ratio === "number" ? `같은 형식 대비 ${times(c.peer_age_ratio)}` : c.baseline_reason ?? ""}
          </span>
        </>
      ) : (
        <b className={`text-sm ${pending?.tone}`}>{pending?.text}</b>
      )}
    </div>
  );
}

function Judge({ v }: { v: VideoPerformanceVideo }) {
  const verdict = VERDICT[v.verdict ?? "excluded"];
  const seven = v.checkpoints.find((c) => c.checkpoint === "7d");
  const target = v.target_views_reference;
  const p = v.projection;
  let big = "—";
  let ratio: number | null = null;
  let sub = v.verdict_reason ?? "";
  if (typeof seven?.target_ratio === "number" && typeof seven.views === "number" && target) {
    ratio = seven.target_ratio;
    big = `목표의 ${times(ratio)}`;
    sub = `${n(seven.views)}회 / 목표 ${n(target)}회`;
  } else if (v.verdict === "measuring" && typeof p?.ratio === "number" && typeof p.views === "number" && target) {
    ratio = p.ratio;
    big = `예상 ${times(ratio)}`;
    sub = `7일 예상 ${n(p.views)}회 (예상치) / 목표 ${n(target)}회`;
  } else if (v.verdict === "measuring" && target) {
    sub = `목표 ${n(target)}회 · ${p?.reason ?? "7일이 아직 안 됨"}`;
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className={`chip w-fit text-xs font-semibold ${verdict.badge}`}>{verdict.label}</span>
      <p className={`text-[22px] font-bold leading-tight ${verdict.text}`}>{big}</p>
      <div className="h-1.5 w-full bg-ink-100" aria-hidden>
        <div className={`h-full ${verdict.bar}`} style={{ width: `${Math.min(ratio ?? 0, 1) * 100}%` }} />
      </div>
      <p className="text-xs text-ink-500">{sub}</p>
    </div>
  );
}

function SignalTile({ name, s }: { name: keyof typeof SIGNAL_NAME; s: VideoSignal }) {
  const level = LEVEL[s.level];
  return (
    <div className="flex min-w-0 flex-col border border-ink-100 bg-surface px-3 py-2" title={s.reason ?? undefined}>
      <span className="text-[11px] text-ink-500">{SIGNAL_NAME[name]}</span>
      <b className="text-base text-ink-900">{s.display ?? "–"}</b>
      <span className={`text-[11px] font-semibold ${s.collecting ? "text-violet-700" : level.text}`}>
        {s.collecting ? (s.reason ?? "기준 모으는 중") : level.label}
      </span>
    </div>
  );
}

const PROGRESS_WHEN = { "1h": "오늘 안에", "1d": "약 1일 뒤", "3d": "약 3일 뒤", "7d": "약 7일 뒤" } as const;

/** 같은 형식 비교 기준 모으는 중 (서버 표본 수 그대로). 모든 시점이 다 모이면 숨긴다 */
function BaselineBox({ list, ready }: { list: BaselineProgress[]; ready: boolean }) {
  if (ready || list.length === 0) return null;
  const order = ["1h", "1d", "3d", "7d"] as const;
  return (
    <section className="card flex flex-col gap-3 border border-ink-100 p-4" aria-labelledby="data-baseline-progress">
      <div>
        <h3 id="data-baseline-progress" className="text-base font-semibold text-ink-900">같은 형식 비교 기준 모으는 중</h3>
        <p className="text-sm text-ink-500">
          새로 올라온 숏츠를 게시 직후부터 추적합니다. 시점마다 5개가 모이면 신호 등급이 켜집니다. 그 전에는 '기준 모으는 중'으로 표시합니다.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {order.map((cp) => {
          const cells = list.filter((p) => p.checkpoint === cp);
          const done = cells.reduce((a, p) => a + Math.min(p.n, p.need), 0);
          const total = cells.reduce((a, p) => a + p.need, 0) || 1;
          return (
            <div key={cp} className="flex min-w-0 flex-col gap-1">
              <span className="text-xs text-ink-500">{CP_NAME[cp]} 기준</span>
              <b className="text-sm text-ink-900">{cells.map((p) => `${p.shorts_format} ${Math.min(p.n, p.need)}/${p.need}`).join(" · ")}</b>
              <div className="h-1.5 w-full bg-ink-100" aria-hidden>
                <div className="h-full bg-violet-500" style={{ width: `${(done / total) * 100}%` }} />
              </div>
              <span className="text-[11px] text-ink-500">{done >= total ? "다 모임" : PROGRESS_WHEN[cp]}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function VideoCard({ v }: { v: VideoPerformanceVideo }) {
  const meta = [v.topic ?? v.insight_id, v.shorts_format, v.target_views_reference ? `목표 ${n(v.target_views_reference)}회` : null]
    .filter(Boolean).join(" · ");
  const order = ["1h", "1d", "3d", "7d"] as const;
  const byName = new Map(v.checkpoints.map((c) => [c.checkpoint, c]));
  const showWhy = !v.test && v.signals;
  return (
    <article className={`card border ${v.test ? "border-dashed border-ink-300 opacity-60" : "border-ink-100"}`}>
      <div className="grid grid-cols-1 items-center gap-4 p-4 lg:grid-cols-[1.1fr_2fr_210px]">
        <div className="min-w-0">
          <h4 className="truncate text-base font-semibold text-ink-900" title={nameOf(v)}>
            {nameOf(v)} {v.test && <span className="chip ml-1 bg-ink-100 text-xs text-ink-500">시험 영상</span>}
          </h4>
          <p className="text-xs text-ink-500">{meta || "인사이트 없음"}</p>
          {!v.test && v.sales_measurable === false && (
            <p className="text-[11px] text-ink-500">매출 효과: {v.sales_reason ?? "측정 불가"}</p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {order.map((k) => {
            const c = byName.get(k);
            return c ? <Checkpoint key={k} c={c} /> : <div key={k} />;
          })}
        </div>
        <Judge v={v} />
      </div>
      {showWhy && v.signals && (
        <div className="grid grid-cols-1 gap-6 border-t border-ink-100 bg-surface-2 p-4 lg:grid-cols-[1.5fr_1fr]">
          <div className="min-w-0">
            <h5 className="mb-2 text-sm font-semibold text-ink-900">성과 요인</h5>
            <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(Object.keys(SIGNAL_NAME) as (keyof typeof SIGNAL_NAME)[]).map((k) => (
                <SignalTile key={k} name={k} s={v.signals![k]} />
              ))}
            </div>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
              {v.factors.map((f) => <li key={f}>{f}</li>)}
            </ul>
          </div>
          <div className="min-w-0">
            <h5 className="mb-2 text-sm font-semibold text-ink-900">개선 방향</h5>
            {v.actions.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700 marker:text-brand-600">
                {v.actions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            ) : <p className="text-sm text-ink-500">지금 고칠 점으로 잡힌 것이 없습니다.</p>}
          </div>
        </div>
      )}
    </article>
  );
}

function InsightCard({ c }: { c: VideoInsightCard }) {
  return (
    <div className="card flex flex-col gap-2 border border-ink-100 p-4">
      <h4 className="text-base font-semibold text-ink-900">{c.topic ?? c.insight_id}</h4>
      <p className="text-sm text-ink-700">{c.line}</p>
      <div className="flex flex-wrap gap-1.5">
        {c.chips.map((chip) => {
          const tone = VERDICT[chip.verdict].badge;
          const value = typeof chip.target_ratio === "number" ? times(chip.target_ratio)
            : typeof chip.projected_ratio === "number" ? `예상 ${times(chip.projected_ratio)} (예상치)`
            : VERDICT[chip.verdict].label;
          return <span key={chip.video_id} className={`chip text-xs font-semibold ${tone}`}>{chip.name} · {value}</span>;
        })}
      </div>
      {typeof c.median_target_ratio === "number" && <p className="text-xs text-ink-500">목표 대비 중앙값 {times(c.median_target_ratio)}</p>}
      {c.notes.map((note) => <p key={note} className="text-xs text-amber-700">{note}</p>)}
    </div>
  );
}

const SALES_BADGE: Record<string, string> = {
  more: "bg-emerald-50 text-emerald-700",
  less: "bg-red-50 text-red-700",
  no_effect: "bg-ink-100 text-ink-500",
};
const SALES_TEXT: Record<string, string> = { more: "text-emerald-700", less: "text-red-700", no_effect: "text-ink-700" };

function SalesCell({ c }: { c: CampaignSalesCell }) {
  return (
    <div className="flex min-w-0 flex-col bg-surface-2 px-3 py-2">
      <span className="text-[11px] text-ink-500">{c.label}</span>
      <b className="text-sm text-ink-900">{c.status === "known" ? c.line : "확인할 수 없음"}</b>
      {c.status !== "known" && c.reason && <span className="text-[11px] text-ink-500">{c.reason}</span>}
    </div>
  );
}

function CampaignCard({ c }: { c: CampaignState }) {
  if (c.kind === "loading") return <div className="card border border-ink-100 p-4"><Waiting what={`캠페인 ${c.id} 매출 효과를 읽는 중…`} /></div>;
  if (c.kind === "failed") return <div className="card border border-ink-100 p-4"><Empty>캠페인 {c.id} 매출 효과를 받지 못했어요. {c.reason}</Empty></div>;
  const out = c.out;
  const sales = out.sales;
  const verdict = sales?.verdict;
  const name = verdict?.verdict ?? "no_effect";
  const loss = verdict?.verdict === "less";
  return (
    <article className={`card flex flex-col gap-2 p-4 ${loss ? "border-2 border-red-600" : "border border-ink-100"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-base font-semibold text-ink-900">
          {out.campaign_name ?? out.campaign_id} <span className="text-xs font-normal text-ink-500">({out.campaign_id})</span>
        </h4>
        <span className={`chip text-xs font-semibold ${SALES_BADGE[name]}`} title={verdict?.low_sample_reason ?? verdict?.reason ?? undefined}>
          {verdict?.label ?? "측정 불가"}
        </span>
      </div>
      <p className={`text-[22px] font-bold leading-tight ${SALES_TEXT[name]}`}>{sales?.headline ?? (sales?.reason ?? out.reason ?? "—")}</p>
      {sales && sales.cells.length > 0 && (
        <div className="grid grid-cols-2 gap-2">{sales.cells.map((cell) => <SalesCell key={cell.key} c={cell} />)}</div>
      )}
      {sales && <p className="text-xs text-ink-500">{sales.measurable_line}</p>}
      {sales?.notes.map((note) => <p key={note} className="text-xs font-semibold text-amber-700">{note}</p>)}
    </article>
  );
}

function CampaignSection({ campaigns }: { campaigns: CampaignState[] }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold text-ink-900">캠페인 매출 효과</h3>
      {campaigns.length === 0
        ? <Empty>아직 매출을 잴 캠페인이 없습니다</Empty>
        : <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{campaigns.map((c) => <CampaignCard key={c.id} c={c} />)}</div>}
      <div className="text-xs leading-relaxed text-ink-500">
        <p>네 칸은 겹치는 매출이라 더하지 않습니다. 표본이 적으면 '효과 확인 안 됨(표본 적음)'으로 표시합니다.</p>
        <p>수익·손실(광고비·제작비 포함)은 재무에서 판단합니다.</p>
      </div>
    </section>
  );
}

function LossAlerts({ campaigns }: { campaigns: CampaignState[] }) {
  const losses = campaigns.flatMap((c) => (c.kind === "ok" ? c.out.alerts.filter((a) => a.kind === "loss") : []));
  if (!losses.length) return null;
  return (
    <div role="alert" className="flex flex-col gap-1">
      {losses.map((a) => (
        <p key={a.message} className="text-base font-bold text-red-700">
          ▲ {a.message} <span className="ml-2 text-xs font-semibold text-ink-700">{a.note}</span>
        </p>
      ))}
    </div>
  );
}

function Result({ out, at, campaigns }: { out: VideoPerformanceListOutput; at: string | null; campaigns: CampaignState[] }) {
  const t = out.tally ?? { videos: 0, achieved: 0, missed: 0, measuring: 0, excluded: 0 };
  const limit = out.limits.find((l) => l.includes("YouTube Analytics"));
  const tiles: [string, number, string][] = [
    ["영상", t.videos, "시험 영상 제외"], ["목표 달성", t.achieved, ""], ["목표 미달", t.missed, ""], ["측정 중", t.measuring, "7일이 아직 안 됨"],
  ];
  return (
    <div className="flex flex-col gap-5">
      <LossAlerts campaigns={campaigns} />
      <p className="text-sm text-ink-500">
        기획이 정한 7일 조회수 목표와 실제 7일 조회수를 비교하고, 숫자로 성과 요인과 개선 방향을 붙입니다. 기준 시각 {when(at)}
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map(([label, value, hint]) => (
          <div key={label} className="card flex flex-col border border-ink-100 px-4 py-3">
            <span className="text-sm text-ink-500">{label}</span>
            <b className="text-[26px] font-bold text-ink-900">{value}개</b>
            <span className="text-xs text-ink-500">{hint || " "}</span>
          </div>
        ))}
      </div>
      <div className="bg-violet-50 px-4 py-3 text-sm leading-relaxed text-violet-900">
        <p><b className="text-violet-700">보는 법</b> ① 판정은 7일 조회수 ÷ 목표 (파란 테두리 칸) ② 아래 회색 칸이 성과 요인과 개선 방향
          ③ 매출 효과는 캠페인 단위로 따로 봅니다. 조회가 잘 돼도 매출은 그대로일 수 있습니다</p>
        <p><b className="text-violet-700">근거 신호 4개</b> 첫 1시간 속도 · 좋아요율 · 댓글율 · 1일 이후 성장. 같은 형식 숏츠의 게시 후 같은 시간대 중앙값과 비교해
          강함(80% 이상) / 보통(50~80%) / 약함(50% 미만), 비교 표본이 5개 미만이면 판단 불가로 표시합니다.</p>
      </div>
      <BaselineBox list={out.baseline_progress ?? []} ready={out.baseline_ready ?? false} />
      <section className="flex flex-col gap-3">
        <h3 className="text-base font-semibold text-ink-900">영상별 결과</h3>
        {out.videos.map((v) => <VideoCard key={`${v.video_id}-${v.test}`} v={v} />)}
      </section>
      <CampaignSection campaigns={campaigns} />
      {out.insight_cards.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-base font-semibold text-ink-900">인사이트별로 보면</h3>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {out.insight_cards.map((c) => <InsightCard key={c.insight_id} c={c} />)}
          </div>
        </section>
      )}
      {limit && <p className="border border-dashed border-ink-300 px-4 py-3 text-sm text-ink-500">{limit}</p>}
      <details className="text-sm">
        <summary className="cursor-pointer select-none text-ink-700">상세 숫자 (좋아요·댓글·게시 후 시간)</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-ink-500">
              <tr><th className="py-1 pr-3">영상</th><th className="pr-3">시점</th><th className="pr-3">조회</th><th className="pr-3">좋아요</th><th className="pr-3">댓글</th><th>게시 후(시간)</th></tr>
            </thead>
            <tbody>
              {out.videos.flatMap((v) => v.checkpoints.map((c) => (
                <tr key={`${v.video_id}-${v.test}-${c.checkpoint}`} className="border-t border-ink-100 text-ink-700">
                  <td className="py-1 pr-3">{nameOf(v)}</td>
                  <td className="pr-3">{CP_NAME[c.checkpoint]}</td>
                  <td className="pr-3">{typeof c.views === "number" ? n(c.views) : "—"}</td>
                  <td className="pr-3">{typeof c.likes === "number" ? n(c.likes) : "—"}</td>
                  <td className="pr-3">{typeof c.comments === "number" ? n(c.comments) : "—"}</td>
                  <td>{typeof c.hours_after === "number" ? c.hours_after : (c.reason ?? "—")}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

export function VideoPerformance() {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>(() =>
    kpiLive ? { kind: "loading" } : { kind: "failed", reason: "데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 영상 성과를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다." },
  );

  const [campaigns, setCampaigns] = useState<CampaignState[]>([]);

  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    setCampaigns([]);
    dataAi.videoPerformance()
      .then((r) => {
        if (!live) return;
        setState({ kind: "ok", out: r.output, at: r.evidence?.collected_at ?? null });
        // 시험 영상이 아닌 영상의 캠페인만 (나온 순서대로, 한 번씩)
        const ids = [...new Set(r.output.videos.filter((v) => !v.test).map((v) => v.campaign_id).filter((x): x is string => !!x))];
        setCampaigns(ids.map((id) => ({ id, kind: "loading" })));
        ids.forEach((id) => {
          dataAi.campaignResult(id)
            .then((c) => live && setCampaigns((all) => all.map((x) => (x.id === id ? { id, kind: "ok", out: c.output } : x))))
            .catch((e: Error) => live && setCampaigns((all) => all.map((x) => (x.id === id ? { id, kind: "failed", reason: e.message } : x))));
        });
      })
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);

  return (
    <Panel id="data-video-performance" title="영상 성과"
      tools={<button className="btn-ghost" onClick={() => setRun((x) => x + 1)} disabled={!kpiLive || state.kind === "loading"}>새로고침</button>}>
      {state.kind === "loading" && <Waiting what="영상 성과를 읽는 중… (저장된 측정값만 읽습니다)" />}
      {state.kind === "failed" && (
        <Empty>영상 성과를 받지 못했어요. {state.reason}{kpiLive ? ` ${DATA_SERVER}가 켜져 있는지 확인해 주세요.` : ""}</Empty>
      )}
      {state.kind === "ok" && (state.out.videos.length === 0
        ? <Empty>아직 측정한 캠페인 영상이 없습니다</Empty>
        : <Result out={state.out} at={state.at} campaigns={campaigns} />)}
    </Panel>
  );
}
