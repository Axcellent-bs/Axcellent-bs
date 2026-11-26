import { useEffect, useState, type FormEvent } from "react";
import { Empty, Panel, Waiting } from "@/components/finance/bits";
import { when } from "@/components/data/Sources";
import { DATA_SERVER, dataAi, kpiLive } from "@/lib/data";
import { sessionCompanyId } from "@/lib/humanSession";
import type { Proposal } from "@/lib/proposals";
import {
  NOT_THIS_COMPANY, SAMPLE_SHORT,
  adView, audienceRows, basisChip, basisLine, biggestDrop, dropLine, joinVideos, measuresVideo, registerLine, registerRequest,
  sourceRows, statCells, targetAudience, trafficName, trafficRows,
  type AdPerformance, type AdView, type PerfVideo, type RetentionPoint, type SourceRow, type SourceStatus, type VideoInsights, type VideoRow,
} from "@/lib/videoDeep";

// 성과 탭 위 "영상 성과" (11/27 시안, 유튜브만): 주소 등록 → 영상 카드(공개 수치·평균 시청 비율·노출 클릭률·유지 곡선·
// 유입 경로·기획 타깃 vs 실제 시청층) → 광고(구글애즈) → 출처 연결 상태. 값은 데이터 AI 응답 그대로(lib/videoDeep.ts),
// 없으면 0 대신 "연결 대기·표본 부족". 인스타·다른 회사 칸은 없다.

type Load<T> = { kind: "loading" } | { kind: "ok"; out: T } | { kind: "failed"; reason: string };
const okOf = <T,>(s: Load<T>): T | null => (s.kind === "ok" ? s.out : null);
const failOf = <T,>(s: Load<T>): string | undefined => (s.kind === "failed" ? s.reason : undefined);
const fmt = (iso: string) => when(iso) ?? iso;
const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

export function VideoDeep({ proposals }: { proposals: readonly Proposal[] }) {
  const company = sessionCompanyId();
  if (!measuresVideo(company)) {
    return <Panel id="data-video-deep" title="영상 성과"><Empty>{NOT_THIS_COMPANY}</Empty></Panel>;
  }
  if (!kpiLive) {
    return (
      <Panel id="data-video-deep" title="영상 성과">
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 영상 성과를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
      </Panel>
    );
  }
  return <Live proposals={proposals} />;
}

function Live({ proposals }: { proposals: readonly Proposal[] }) {
  const [run, setRun] = useState(0);
  const [perf, setPerf] = useState<Load<PerfVideo[]>>({ kind: "loading" });
  const [ins, setIns] = useState<Load<VideoInsights>>({ kind: "loading" });
  const [ad, setAd] = useState<Load<AdPerformance>>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    setPerf({ kind: "loading" });
    setIns({ kind: "loading" });
    setAd({ kind: "loading" });
    dataAi.videoPerformance()
      .then((r) => live && setPerf({ kind: "ok", out: r.output.videos }))
      .catch((e: Error) => live && setPerf({ kind: "failed", reason: e.message }));
    dataAi.videoInsights()
      .then((r) => live && setIns({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setIns({ kind: "failed", reason: e.message }));
    dataAi.adPerformance()
      .then((r) => live && setAd({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setAd({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);

  const insOut = okOf(ins);
  const adOut = okOf(ad);
  const rows = perf.kind === "ok" ? joinVideos(perf.out, insOut) : [];
  const loading = perf.kind === "loading" || ins.kind === "loading";
  return (
    <Panel id="data-video-deep" title="영상 성과"
      hint="AXcellent 유튜브(@Axcellent7)에 올린 영상의 공개 수치와 유튜브 분석 값. 저장된 값만 읽어요 (공개 수치 1시간마다, 유튜브 분석 하루 1번)."
      tools={<button className="btn-ghost" onClick={() => setRun((x) => x + 1)} disabled={loading}>새로고침</button>}>
      <div className="flex flex-col gap-4">
        <Register onDone={() => setRun((x) => x + 1)} />
        {perf.kind === "loading" && <Waiting what="영상 성과를 읽는 중… (저장된 값만 읽습니다)" />}
        {perf.kind === "failed" && <Empty>영상 공개 수치를 받지 못했어요. {perf.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>}
        {perf.kind === "ok" && rows.length === 0 && <Empty>아직 등록한 유튜브 영상이 없어요. 위에 영상 주소를 넣어 주세요</Empty>}
        {ins.kind === "failed" && <p className="text-xs text-amber-700">유튜브 분석 값을 받지 못했어요 · {ins.reason}</p>}
        {rows.map((r) => <VideoCard key={r.perf.video_id ?? r.perf.video_url} row={r} target={targetAudience(proposals, r.perf.campaign_id)} />)}
        <AdBox view={ad.kind === "loading" ? null : adView(adOut, failOf(ad))} />
        <SourceTable rows={perf.kind === "loading" || ins.kind === "loading" || ad.kind === "loading" ? null
          : sourceRows(okOf(perf) ?? [], insOut, adOut, failOf(ad))} />
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- ① 등록

function Register({ onDone }: { onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [campaign, setCampaign] = useState("");
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const req = registerRequest(url, campaign);
    if (!req.ok) { setLine({ ok: false, text: req.message }); return; }
    setBusy(true);
    setLine(null);
    dataAi.videoRegister(req.input)
      .then((r) => {
        const out = registerLine(r.output);
        setLine(out);
        if (out.ok) { setUrl(""); setCampaign(""); onDone(); }
      })
      .catch((err: Error) => setLine({ ok: false, text: `등록하지 못했어요 · ${err.message}` }))
      .finally(() => setBusy(false));
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-2 border border-ink-100 p-3">
      <h3 className="text-sm font-semibold text-ink-900">영상 주소 등록</h3>
      <p className="text-xs text-ink-500">이미 올린 유튜브 영상(숏츠 포함) 주소를 넣으면 성과를 재기 시작해요.</p>
      <div className="flex flex-wrap gap-2">
        <input className="field min-w-0 flex-[2_1_220px]" type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="https://youtube.com/shorts/…" aria-label="유튜브 영상 주소" disabled={busy} />
        <input className="field min-w-0 flex-[1_1_140px]" type="text" value={campaign} onChange={(e) => setCampaign(e.target.value)}
          placeholder="캠페인 번호 (선택)" aria-label="캠페인 번호 (선택)" maxLength={120} disabled={busy} />
        <button className="btn-primary" type="submit" disabled={busy}>{busy ? "등록 중…" : "등록"}</button>
      </div>
      {line && <p role="status" className={`text-sm ${line.ok ? "text-emerald-700" : "text-amber-700"}`}>{line.text}</p>}
    </form>
  );
}

// ---------------------------------------------------------------- ② 영상 카드

function VideoCard({ row, target }: { row: VideoRow; target: string | null }) {
  const { perf, ins } = row;
  const known = ins?.status === "known" ? ins : null;
  const cells = statCells(perf, ins);
  return (
    <article className="card flex min-w-0 flex-col gap-3 p-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="chip border border-red-700 text-red-700">YouTube</span>
          {perf.campaign_id && <span className="chip bg-ink-100 text-ink-700">캠페인 {perf.campaign_id}</span>}
        </div>
        <h3 className="break-words text-base font-semibold text-ink-900">
          {perf.video_url ? <a className="hover:underline" href={perf.video_url} target="_blank" rel="noreferrer">{perf.title || perf.video_id}</a> : perf.title || perf.video_id}
        </h3>
        <p className="text-xs text-ink-500">{basisLine(perf, ins, fmt)}</p>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {cells.map((c) => (
          <div key={c.key} className="flex min-w-0 flex-col border border-ink-100 px-3 py-2">
            <span className="text-xs text-ink-500">{c.label}</span>
            {c.value !== null
              ? <b className="text-lg text-ink-900 tabular-nums">{c.value}</b>
              : <b className="text-sm text-amber-700">{c.missing}</b>}
            <span className="text-[11px] text-ink-500">{c.sub}</span>
          </div>
        ))}
      </div>
      {known && known.retention.length >= 2 && <Retention points={known.retention} />}
      {known && (
        <div className="grid grid-cols-1 gap-4 border-t border-ink-100 pt-3 md:grid-cols-2">
          <Traffic ins={known} />
          <Audience ins={known} target={target} />
        </div>
      )}
      {!known && ins?.reason && <p className="border border-dashed border-ink-300 px-3 py-2 text-xs text-ink-500">유튜브 분석: {ins.reason}</p>}
    </article>
  );
}

// ---------------------------------------------------------------- ③ 유지 곡선

function Retention({ points }: { points: RetentionPoint[] }) {
  const pts = [...points].sort((a, b) => a.ratio - b.ratio);
  const top = Math.max(1, ...pts.map((p) => p.watch_ratio));
  const x = (r: number) => 40 + r * 550;
  const y = (w: number) => 16 + (1 - w / top) * 104;
  const drop = biggestDrop(pts);
  const label = `유지 곡선: ${pts.map((p) => `${Math.round(p.ratio * 100)}% 지점 ${Math.round(p.watch_ratio * 100)}%`).join(", ")}`;
  return (
    <section className="flex flex-col gap-1 border-t border-ink-100 pt-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-sm font-semibold text-ink-700">시청 유지 곡선</h4>
        <span className="text-xs text-ink-500">영상 길이 비율마다 본 사람 비율 (유튜브 분석 값)</span>
      </div>
      <svg viewBox="0 0 600 140" className="block h-auto w-full" role="img" aria-label={label}>
        <g className="text-ink-100" stroke="currentColor">
          {[1, 0.5, 0].map((w) => <line key={w} x1="40" x2="590" y1={y(w)} y2={y(w)} />)}
        </g>
        <g className="fill-ink-500 text-[11px]">
          {[1, 0.5, 0].map((w) => <text key={w} x="34" y={y(w) + 4} textAnchor="end">{Math.round(w * 100)}%</text>)}
          <text x="40" y="136">시작</text>
          <text x="590" y="136" textAnchor="end">끝</text>
        </g>
        {drop && (
          <g className="text-amber-700">
            <rect x={x(drop.from)} y="16" width={Math.max(2, x(drop.to) - x(drop.from))} height="104" fill="currentColor" opacity="0.12" />
            <line x1={x(drop.from)} x2={x(drop.from)} y1="16" y2="120" stroke="currentColor" strokeDasharray="3 3" />
          </g>
        )}
        <polyline className="text-brand-600" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"
          points={pts.map((p) => `${x(p.ratio)},${y(p.watch_ratio)}`).join(" ")} />
      </svg>
      {drop && <p className="text-xs font-semibold text-amber-700">가장 크게 빠진 곳 · {dropLine(drop)}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- ④ 유입 경로 · ⑤ 시청층

function Bar({ name, value, width, title, tone = "bg-brand-600" }: { name: string; value: string; width: number; title?: string; tone?: string }) {
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)_52px] items-center gap-2 text-[13px]" title={title}>
      <span className="truncate text-ink-700">{name}</span>
      <i className="relative block h-2.5 overflow-hidden bg-ink-100" aria-hidden>
        <b className={`absolute inset-y-0 left-0 ${tone}`} style={{ width: `${Math.max(0, Math.min(width, 1)) * 100}%` }} />
      </i>
      <em className="text-right not-italic tabular-nums text-ink-700">{value}</em>
    </div>
  );
}

function Traffic({ ins }: { ins: VideoInsights }) {
  const rows = trafficRows(ins.traffic);
  const max = Math.max(1, ...rows.map((r) => r.views));
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h4 className="text-sm font-semibold text-ink-700">유입 경로</h4>
      {rows.length === 0 ? <p className="text-xs text-ink-500">유입 경로 값이 아직 없어요</p> : (
        <div className="flex flex-col gap-1.5">
          {rows.map((r) => (
            <Bar key={r.source} name={trafficName(r.source)} title={`${r.source} · 조회 ${r.views.toLocaleString("ko-KR")}`}
              value={typeof r.share === "number" ? pct(r.share) : r.views.toLocaleString("ko-KR")}
              width={typeof r.share === "number" ? r.share : r.views / max} />
          ))}
        </div>
      )}
    </section>
  );
}

function Audience({ ins, target }: { ins: VideoInsights; target: string | null }) {
  const chip = basisChip(ins.audience_basis);
  const rows = audienceRows(ins.audience);
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <h4 className="text-sm font-semibold text-ink-700">기획 타깃 vs 실제 시청층</h4>
        {chip && <span className={`chip ${ins.audience_basis === "account" ? "bg-amber-50 text-amber-700" : "bg-ink-100 text-ink-700"}`}>{chip}</span>}
      </div>
      <p className="text-xs text-ink-500">
        기획 타깃 <b className="font-semibold text-emerald-700">{target ?? "기록 없음"}</b>
        {!target && " (이 영상 캠페인의 기획 제안에 타깃이 없어요)"}
      </p>
      {rows.length === 0
        ? <p className="text-xs text-amber-700">실제 시청층 · {ins.audience_reason ?? SAMPLE_SHORT}</p>
        : <div className="flex flex-col gap-1.5">{rows.map((r) => <Bar key={r.name} name={r.name} value={`${r.percent}%`} width={r.percent / 100} />)}</div>}
    </section>
  );
}

// ---------------------------------------------------------------- ⑥ 광고 · ⑦ 출처 연결 상태

function AdBox({ view }: { view: AdView | null }) {
  return (
    <section className="flex flex-col gap-2 border border-ink-100 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <h3 className="text-sm font-semibold text-ink-900">광고</h3>
        <span className="chip border border-brand-600 text-brand-700">Google Ads</span>
        <span className="text-xs text-ink-500">읽기만</span>
      </div>
      {view === null && <p className="text-xs text-ink-500" aria-live="polite">광고 성과를 읽는 중…</p>}
      {view?.kind === "waiting" && (
        <div className="border border-dashed border-ink-300 px-3 py-2 text-sm text-ink-500">
          <b className="text-ink-700">구글애즈 연결 대기</b> · {view.reason}
          {view.needs.length > 0 && <span className="block text-xs">필요한 것: {view.needs.join(" · ")}</span>}
        </div>
      )}
      {view?.kind === "rows" && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] text-left text-[13px]">
            <thead className="text-xs text-ink-500">
              <tr><th className="py-1 pr-2 font-medium">캠페인</th><th className="pr-2 font-medium">비용</th><th className="pr-2 font-medium">노출</th><th className="pr-2 font-medium">조회</th><th className="font-medium">조회율</th></tr>
            </thead>
            <tbody>
              {view.rows.map((c) => (
                <tr key={c.campaign_id} className="border-t border-ink-100 text-ink-700">
                  <td className="py-1.5 pr-2">{c.name || c.campaign_id}</td>
                  <td className="pr-2 tabular-nums">{typeof c.cost === "number" ? `${c.cost.toLocaleString("ko-KR")} ${c.currency ?? ""}` : "—"}</td>
                  <td className="pr-2 tabular-nums">{typeof c.impressions === "number" ? c.impressions.toLocaleString("ko-KR") : "—"}</td>
                  <td className="pr-2 tabular-nums">{typeof c.views === "number" ? c.views.toLocaleString("ko-KR") : "—"}</td>
                  <td className="tabular-nums">{typeof c.view_rate === "number" ? pct(c.view_rate) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {view.period && <p className="mt-1 text-xs text-ink-500">기간 {view.period}</p>}
        </div>
      )}
    </section>
  );
}

const STATUS_CHIP: Record<SourceStatus, string> = {
  connected: "bg-emerald-50 text-emerald-700",
  waiting: "bg-amber-50 text-amber-700",
  check: "bg-amber-50 text-amber-700",
};

function SourceTable({ rows }: { rows: SourceRow[] | null }) {
  return (
    <section className="flex flex-col gap-2 border border-ink-100 p-3">
      <h3 className="text-sm font-semibold text-ink-900">출처 연결 상태</h3>
      {rows === null ? <p className="text-xs text-ink-500" aria-live="polite">연결 상태를 읽는 중…</p> : (
        <ul className="flex flex-col">
          {rows.map((r) => (
            <li key={r.name} className="grid grid-cols-1 gap-1 border-t border-ink-100 py-2 first:border-t-0 sm:grid-cols-[150px_minmax(0,1fr)_minmax(0,1.2fr)] sm:gap-3">
              <span className="text-sm font-semibold text-ink-900">{r.name}</span>
              <span className="text-xs text-ink-500 sm:text-sm">{r.reads}</span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className={`chip w-fit max-w-full whitespace-normal break-words ${STATUS_CHIP[r.status]}`}>{r.label}</span>
                {r.needs.length > 0 && <span className="break-words text-xs text-ink-500">필요한 것: {r.needs.join(" · ")}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-ink-500">토큰 값은 보이지 않아요. 연결 상태와 필요한 것(서버 설정 이름)만.</p>
    </section>
  );
}
