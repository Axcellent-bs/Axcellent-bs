import { useCallback, useEffect, useState, type ReactNode } from "react";
import { TONE, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { when } from "@/components/data/Sources";
import { AD, times } from "@/components/data/TrendMemes";
import { safeLink } from "@/components/data/InstagramAudio";
import { PostCard } from "@/components/data/InstagramRadar";
import { dataAi, type SectionStatus, type TrendReport, type TrendReportCompetitorRow, type TrendReportSections } from "@/lib/data";
import { thumbSrc } from "@/lib/igRadarGrid";
import { sectionState, type SectionState } from "@/lib/segmentView";
import {
  BEAT_LABEL, CALLER_LABEL, EVIDENCE, REPORT_POLL_MS, REPORT_STATUS, SECTION_EVIDENCE, SECTION_STATUS,
  barPct, keepPolling, pctText, shareText, sortBeats, sourceLabel, targetText, type ChipTone, type EvidenceKind, type ReportPart,
} from "@/lib/trendReportView";

// 트렌드 분석 의뢰 보고서 하나 (trend_report_get). 주소 #request?id=<report_id> (분석 의뢰 탭, TrendRequest.tsx가 연다).
// - 진행 중(queued·running)이면 섹션별 진행 칩을 보여 주고 5초마다 다시 읽는다. 완료·부분·실패·중단이면 멈춘다
// - 헤더: 의뢰 요약 5칸(칸마다 출처) · 근거 범례 칩 · 한 줄 결론(없으면 이유 회색)
// - 1 검색(왼쪽) · 2 해시태그(오른쪽) 2단 → 3 콘텐츠(인스타 레이더 카드 그리드 재사용 + 같이 뜬 밈) → 4 경쟁사 → 5 추천 콘티 → 근거와 한계
// - 섹션 status가 known이 아니면 내용 대신 서버 이유(대기·진행이면 진행 표시)
// - 숫자는 서버 값 그대로. 외부 글(캡션·밈 설명)은 텍스트로만, 링크는 safeLink 통과한 것만
// - "PDF로 저장" = window.print(). 인쇄할 때는 이 보고서만 남기고(styles.css .trend-report-print) 버튼·메뉴를 숨긴다

const tone = (t: ChipTone) => TONE[t];
const n = (v: number) => v.toLocaleString("ko-KR");
const PRINT_CLASS = "print-trend-report";

type Load = { kind: "loading" } | { kind: "ok"; report: TrendReport } | { kind: "error"; state: SectionState };

/** 보고서를 읽고, 진행 중이면 5초마다 다시 읽는다 */
function useReport(id: string): { load: Load; reload: () => void } {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  useEffect(() => setLoad({ kind: "loading" }), [id]);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    dataAi.trendReportGet(id)
      .then((r) => {
        if (!live) return;
        setLoad({ kind: "ok", report: r.output });
        if (keepPolling(r.output.status)) timer = setTimeout(() => setTick((x) => x + 1), REPORT_POLL_MS);
      })
      .catch((e: Error) => { if (live) setLoad({ kind: "error", state: sectionState(e.message) }); });
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [id, tick]);
  return { load, reload };
}

function StatusChip({ label, t }: { label: string; t: ChipTone }) {
  return <span className={`chip text-xs ${tone(t)}`}>{label}</span>;
}

function EvidenceChip({ kind }: { kind: EvidenceKind }) {
  const e = EVIDENCE[kind];
  return <span className={`chip text-[11px] ${tone(e.tone)}`}>{e.label}</span>;
}

/** 섹션 틀: 머리(번호·제목·근거 칩·상태) + known이면 내용, 아니면 서버 이유 */
function Part({ no, title, part, status, reason, children }: {
  no?: number; title: string; part: ReportPart; status: SectionStatus; reason: string | null; children: ReactNode;
}) {
  const s = SECTION_STATUS[status];
  return (
    <section className="card min-w-0 space-y-3 p-4 sm:p-5 trend-report-section" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-ink-900">{no != null && <span className="mr-1.5 text-ink-400">{no}</span>}{title}</h3>
        <EvidenceChip kind={SECTION_EVIDENCE[part]} />
        {status !== "known" && <StatusChip label={s.label} t={s.tone} />}
      </div>
      {status === "known" ? children
        : status === "waiting" || status === "running" ? (
          <p className="text-sm text-ink-500" aria-live="polite">{status === "running" ? "만드는 중이에요…" : "앞 단계를 기다리는 중이에요."}</p>
        ) : (
          <p className="text-sm text-ink-500">{reason ?? "이유를 받지 못했어요"}</p>
        )}
    </section>
  );
}

const SECTION_NAMES: [keyof TrendReportSections, string][] = [
  ["search", "검색"], ["hashtags", "해시태그"], ["content", "콘텐츠"], ["competitors", "경쟁사"], ["recommendation", "추천"],
];

function Progress({ report }: { report: TrendReport }) {
  return (
    <ul className="flex flex-wrap gap-2 print-hide" aria-label="섹션별 진행">
      {SECTION_NAMES.map(([k, label]) => {
        const s = SECTION_STATUS[report.sections[k].status];
        return <li key={k}><span className={`chip text-xs ${tone(s.tone)}`}>{label} · {s.label}</span></li>;
      })}
    </ul>
  );
}

function BriefGrid({ report }: { report: TrendReport }) {
  const b = report.brief;
  const cells: [string, string, string][] = [
    ["제품", b.product ?? "정하지 않음", sourceLabel(b.sources.product)],
    ["타깃", targetText(b.target_gender, b.target_ages), sourceLabel(b.sources.target)],
    ["결과물", b.format ?? "정하지 않음", sourceLabel(b.sources.format)],
    ["경쟁사", b.competitors.length ? b.competitors.join(", ") : "없음", sourceLabel(b.sources.competitors)],
    ["키워드", b.keywords.length ? b.keywords.join(", ") : "없음", sourceLabel(b.sources.keywords)],
  ];
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
      {cells.map(([k, v, src]) => (
        <div key={k} className="min-w-0 rounded-lg bg-surface-2 px-3 py-2">
          <dt className="text-xs text-ink-500">{k}</dt>
          <dd className="break-words text-sm font-medium text-ink-900">{v}</dd>
          <dd className="text-[11px] text-ink-400">{src}</dd>
        </div>
      ))}
    </dl>
  );
}

function Pct({ v, reason }: { v: number | null; reason: string | null }) {
  const p = pctText(v, reason);
  return p.known
    ? <span className={`tabular-nums font-semibold ${v! > 0 ? "text-emerald-700" : v! < 0 ? "text-red-700" : "text-ink-700"}`}>{p.text}</span>
    : <span className="text-xs text-ink-500">{p.text}</span>;
}

function SearchPart({ s }: { s: TrendReportSections["search"] }) {
  const max = Math.max(0, ...s.rows.flatMap((r) => [r.search_change_pct, r.shopping_change_pct]).map((v) => (typeof v === "number" ? Math.abs(v) : 0)));
  return (
    <Part no={1} title="검색" part="search" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">볼 키워드가 없어요.</p> : (
        <ul className="space-y-3">
          {s.rows.map((r) => (
            <li key={r.keyword} className="space-y-1">
              <p className="text-sm font-medium text-ink-900">{r.keyword}</p>
              {([["검색", r.search_change_pct, r.search_reason, "bg-brand-500"], ["쇼핑", r.shopping_change_pct, r.shopping_reason, "bg-emerald-500"]] as const).map(([label, v, why, color]) => (
                <div key={label} className="flex items-center gap-2 text-xs">
                  <span className="w-8 shrink-0 text-ink-500">{label}</span>
                  <span className="h-2 min-w-0 flex-1 rounded-full bg-ink-100" aria-hidden="true">
                    <span className={`block h-2 rounded-full ${v != null && v < 0 ? "bg-red-400" : color}`} style={{ width: `${barPct(v, max)}%` }} />
                  </span>
                  <span className="w-40 shrink-0 text-right"><Pct v={v} reason={why} /></span>
                </div>
              ))}
            </li>
          ))}
        </ul>
      )}
      {s.filter_note && <p className="text-xs text-ink-500">{s.filter_note}</p>}
    </Part>
  );
}

function HashtagPart({ s }: { s: TrendReportSections["hashtags"] }) {
  return (
    <Part no={2} title="해시태그" part="hashtags" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">해시태그가 없어요.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-500">
              <tr><th className="py-1 text-left font-normal">해시태그</th><th className="py-1 text-right font-normal">쓴 계정</th><th className="py-1 text-right font-normal">지난주</th><th className="py-1 text-right font-normal">터진 게시물</th></tr>
            </thead>
            <tbody className="tabular-nums">
              {s.rows.map((r) => (
                <tr key={r.tag} className="border-t border-ink-100">
                  <td className="break-all py-1.5 text-ink-900">#{r.tag.replace(/^#/, "")}</td>
                  <td className="py-1.5 text-right">{n(r.accounts)}</td>
                  <td className="py-1.5 text-right text-ink-500">{n(r.previous_accounts)}</td>
                  <td className="py-1.5 text-right">{n(r.burst_posts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Part>
  );
}

function ContentPart({ s }: { s: TrendReportSections["content"] }) {
  return (
    <Part no={3} title="콘텐츠" part="content" status={s.status} reason={s.reason}>
      {s.posts.length === 0 ? <p className="text-sm text-ink-500">갑자기 뜬 게시물이 없어요.</p> : (
        <section aria-label="갑자기 뜬 게시물">
          <h4 className="mb-1.5 text-sm font-semibold text-ink-900">갑자기 뜬 게시물 <small className="font-normal text-ink-500">평소 대비 배수 · {n(s.posts.length)}개</small></h4>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {s.posts.map((p, i) => <PostCard key={p.media_id} post={p} rank={i + 1} />)}
          </ul>
        </section>
      )}
      {s.memes.length > 0 && (
        <section aria-label="같이 뜬 밈" className="space-y-1.5">
          <h4 className="text-sm font-semibold text-ink-900">같이 뜬 밈</h4>
          <ul className="space-y-1.5">
            {s.memes.map((m) => {
              const ad = AD[m.ad_material] ?? AD.unknown;
              return (
                <li key={m.name} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                  <b className="text-ink-900">{m.name}</b>
                  <span className={`chip text-[11px] ${ad.tone}`}>광고 재료 {ad.label}</span>
                  {m.description && <span className="w-full whitespace-pre-line break-words text-xs text-ink-500">{m.description}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </Part>
  );
}

function BestPost({ r }: { r: TrendReportCompetitorRow }) {
  const p = r.best_post;
  if (!p) return <span className="text-xs text-ink-500">-</span>;
  const link = safeLink(p.permalink);
  const src = thumbSrc(p.thumbnail_url);
  const body = (
    <span className="inline-flex items-center gap-2">
      {src ? <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-10 w-8 shrink-0 rounded object-cover" />
        : <span className="h-10 w-8 shrink-0 rounded bg-ink-100" aria-hidden="true" />}
      <span className="tabular-nums font-semibold">×{p.ratio.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</span>
    </span>
  );
  return link ? (
    <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline" aria-label={`${r.account} 최고 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{body}</a>
  ) : body;
}

function CompetitorPart({ s }: { s: TrendReportSections["competitors"] }) {
  return (
    <Part no={4} title="경쟁사" part="competitors" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">읽은 경쟁사 계정이 없어요.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-xs text-ink-500">
              <tr>
                <th className="py-1 text-left font-normal">경쟁사</th><th className="py-1 text-right font-normal">주당 게시</th>
                <th className="py-1 text-right font-normal">릴스 비율</th><th className="py-1 text-right font-normal">릴스 조회 중앙값</th>
                <th className="py-1 pl-3 text-left font-normal">최고 게시물</th><th className="py-1 text-left font-normal">잘 된 게시물 공통점</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {s.rows.map((r) => (
                <tr key={r.account} className="border-t border-ink-100 align-top">
                  <td className="py-1.5 font-medium text-ink-900">
                    {r.account}
                    {r.reason && <span className="block text-xs font-normal text-ink-500">{r.reason}</span>}
                  </td>
                  <td className="py-1.5 text-right">{r.posts_per_week != null ? r.posts_per_week.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "-"}</td>
                  <td className="py-1.5 text-right">{shareText(r.reels_share) ?? "-"}</td>
                  <td className="py-1.5 text-right">{r.reels_view_median != null ? n(r.reels_view_median) : "-"}</td>
                  <td className="py-1.5 pl-3"><BestPost r={r} /></td>
                  <td className="py-1.5 text-xs text-ink-700">{r.traits.length ? r.traits.join(" · ") : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {s.accounts_failed.length > 0 && (
        <ul className="space-y-0.5">
          {s.accounts_failed.map((f) => <li key={f.account} className="text-xs text-ink-500">{f.account} · {f.reason}</li>)}
        </ul>
      )}
    </Part>
  );
}

function RecommendationPart({ s }: { s: TrendReportSections["recommendation"] }) {
  return (
    <Part no={5} title="추천" part="recommendation" status={s.status} reason={s.reason}>
      {s.conclusion && <p className="rounded-md bg-brand-50 px-3 py-2 text-sm font-semibold text-brand-700">{s.conclusion}</p>}
      <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {sortBeats(s.beats).map((b) => (
          <li key={b.slot} className="min-w-0 rounded-lg border border-ink-100 px-3 py-2">
            <p className="text-xs font-semibold text-ink-500">{BEAT_LABEL[b.slot] ?? b.slot}</p>
            <p className="mt-1 whitespace-pre-line break-words text-sm text-ink-900">{b.text}</p>
            {b.evidence.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">{b.evidence.map((e, i) => <li key={i} className="text-[11px] text-ink-500">{e}</li>)}</ul>
            )}
          </li>
        ))}
      </ol>
      {s.music_note && <p className="text-xs text-ink-700">음악 · {s.music_note}</p>}
    </Part>
  );
}

function printReport() {
  const body = document.body;
  body.classList.add(PRINT_CLASS);
  const done = () => { body.classList.remove(PRINT_CLASS); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}

export function TrendReportBody({ report }: { report: TrendReport }) {
  const st = REPORT_STATUS[report.status];
  const sec = report.sections;
  return (
    <article className="trend-report-print space-y-4" aria-label="트렌드 분석 보고서">
      <header className="card space-y-3 p-4 sm:p-5 trend-report-section">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-bold text-ink-900">트렌드 분석 보고서{report.brief.product ? ` · ${report.brief.product}` : ""}</h2>
          <StatusChip label={st.label} t={st.tone} />
          <span className="text-xs text-ink-500">
            {when(report.created_at)} 의뢰 · {CALLER_LABEL[report.caller] ?? report.caller}
            {report.finished_at && ` · ${when(report.finished_at)} 끝남`}
          </span>
          <span className="ml-auto flex flex-wrap gap-2 print-hide">
            <button type="button" className="btn-primary" onClick={printReport}>PDF로 저장</button>
            <button type="button" className="btn-ghost" disabled title="다음 단계">기획으로 넘기기</button>
          </span>
        </div>
        {keepPolling(report.status) && <Progress report={report} />}
        <BriefGrid report={report} />
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
          근거 <EvidenceChip kind="api" /><EvidenceChip kind="ai" /><EvidenceChip kind="human" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <EvidenceChip kind={SECTION_EVIDENCE.summary} />
          {report.summary_line
            ? <p className="text-base font-semibold text-ink-900">{report.summary_line}</p>
            : <p className="text-sm text-ink-500">{report.summary_reason ?? "한 줄 결론이 아직 없어요"}</p>}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SearchPart s={sec.search} />
        <HashtagPart s={sec.hashtags} />
      </div>
      <ContentPart s={sec.content} />
      <CompetitorPart s={sec.competitors} />
      <section className="card space-y-1 border-dashed p-4 opacity-70 print-hide" aria-label="다음 단계">
        <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink-700">영상 안쪽 분석 · 유료 광고 — 담당자 업로드 <PreparingChip /></p>
        <p className="text-xs text-ink-500">다음 단계에서 담당자가 올린 영상·광고 자료로 채웁니다.</p>
      </section>
      <RecommendationPart s={sec.recommendation} />

      <section className="card space-y-3 p-4 sm:p-5 trend-report-section" aria-label="근거와 한계">
        <h3 className="text-base font-semibold text-ink-900">근거와 한계</h3>
        {report.limits.length > 0 ? (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {report.limits.map((l, i) => <li key={i} className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-ink-700">{l}</li>)}
          </ul>
        ) : <p className="text-sm text-ink-500">적힌 한계가 없어요.</p>}
        <p className="text-xs text-ink-500 tabular-nums">호출 · 인스타 {n(report.calls.instagram)}번 · 네이버 {n(report.calls.naver)}번 · AI {n(report.calls.llm)}번</p>
      </section>
    </article>
  );
}

/** 보고서 id로 읽어 그린다. 서버에 skill이 아직 없으면(403) "준비 중" */
export function TrendReportView({ id }: { id: string }) {
  const { load, reload } = useReport(id);
  if (load.kind === "loading") return <Waiting what="보고서를 불러오는 중…" />;
  if (load.kind === "error") {
    return load.state.kind === "missing" ? (
      <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
        <PreparingChip /> 분석 보고서 기능을 데이터 AI에 붙이는 중이에요. 준비되면 여기에 나옵니다.
      </p>
    ) : (
      <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">
        <span className="min-w-0 flex-1">분석 보고서: {load.state.message}</span>
        <button type="button" className="btn-ghost" onClick={reload}>다시 시도</button>
      </div>
    );
  }
  return <TrendReportBody report={load.report} />;
}
