import { useCallback, useEffect, useState, type ReactNode } from "react";
import { TONE, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { when } from "@/components/data/Sources";
import { AD, times } from "@/components/data/TrendMemes";
import { safeLink } from "@/lib/safeLink";
import { PostCard } from "@/components/data/InstagramRadar";
import { dataAi, type SectionStatus, type TrendReport, type TrendReportBeat, type TrendReportBrief, type TrendReportCompetitorRow, type TrendReportSections } from "@/lib/data";
import { thumbSrc } from "@/lib/igRadarGrid";
import { sectionState, type SectionState } from "@/lib/segmentView";
import {
  BEAT_LABEL, CALLER_LABEL, EVIDENCE, HASHTAG_HIDDEN_NOTE, REPORT_POLL_MS, REPORT_STATUS, SECTION_EVIDENCE, SECTION_STATUS,
  barPct, beatEvidence, beatWidths, competitorChip, competitorSummary, evidenceDomId, evidenceExpanded, evidenceListClass, hashtagMeta,
  headerRows, keepPolling, keyEvidence, limitGroups, reportLabel, searchMain, searchNotes, shareText, sortBeats, sortCompetitors, sortSearchRows,
  traitLines, type ChipTone, type EvidenceKind, type ReportPart,
} from "@/lib/trendReportView";

// 트렌드 분석 의뢰 보고서 하나 (trend_report_get). 주소 #request?id=<report_id> (분석 의뢰 탭, TrendRequest.tsx가 연다).
// - 진행 중(queued·running)이면 섹션별 진행 칩을 보여 주고 5초마다 다시 읽는다. 완료·부분·실패·중단이면 멈춘다
// - 헤더: "항목 | 칩" 목록(추정·프리셋 출처만 꼬리표) · "의뢰 고쳐서 다시 만들기"(TrendRequest가 폼을 연다)
// - 위에서 아래로: 헤더 → AI 추천(콘티 타임라인) → "근거 데이터"(범례 칩 + 1 검색 · 2 해시태그 · 3 콘텐츠 · 4 경쟁사) → 근거와 한계
// - 근거 id("search:1")는 근거 데이터 행의 DOM id(tr-ev-search-1)로 이어진다. 인쇄할 때는 근거 목록을 펼친다
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

function SourceTag({ text }: { text: string | null }) {
  return text ? <span className="chip bg-ink-100 text-[11px] text-ink-500">{text}</span> : null;
}

/** 헤더: 항목 | 칩 목록 */
function BriefList({ brief: b }: { brief: TrendReportBrief }) {
  return (
    <dl className="space-y-2">
      {headerRows(b).map((row) => (
        <div key={row.key} className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-start gap-x-3 gap-y-1">
          <dt className="pt-0.5 text-xs text-ink-500">{row.label}</dt>
          <dd className="flex min-w-0 flex-wrap items-center gap-1.5">
            {row.key === "product" ? (
              <>
                <span className="break-words text-base font-bold text-ink-900">{b.product ?? "정하지 않음"}</span>
                {b.goal && <span className="chip bg-brand-50 text-brand-700">목표 · {b.goal}</span>}
              </>
            ) : row.chips.length === 0 ? <span className="text-sm text-ink-500">없음</span>
              : row.chips.map((c, i) => (
                <span key={i} className="chip bg-ink-100 text-ink-900">
                  {c.text}{c.sub && <small className="font-normal text-ink-500">{c.sub}</small>}
                </span>
              ))}
            <SourceTag text={row.sourceTag} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** 근거 행으로 스크롤하고 잠깐 강조 */
function jumpToEvidence(id: string) {
  const el = document.getElementById(evidenceDomId(id));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const cls = ["ring-2", "ring-brand-500", "bg-brand-50"];
  el.classList.add(...cls);
  setTimeout(() => el.classList.remove(...cls), 1800);
}

function SearchPart({ s, brief }: { s: TrendReportSections["search"]; brief: TrendReportBrief }) {
  const rows = sortSearchRows(s.rows);
  const max = Math.max(0, ...rows.map((r) => searchMain(r).bar).map((v) => (typeof v === "number" ? Math.abs(v) : 0)));
  return (
    <Part no={1} title="검색" part="search" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">볼 키워드가 없어요.</p> : (
        <ul className="space-y-3">
          {rows.map((r) => {
            const m = searchMain(r);
            const notes = searchNotes(r, brief);
            const neg = m.bar != null && m.bar < 0;
            return (
              <li key={r.keyword} id={evidenceDomId(`search:${s.rows.indexOf(r) + 1}`)} className="space-y-0.5 px-1 transition">
                <div className="flex items-center gap-2 text-sm">
                  <span className="w-28 shrink-0 truncate font-medium text-ink-900 sm:w-36" title={r.keyword}>{r.keyword}</span>
                  <span className="relative flex h-2 min-w-0 flex-1 bg-ink-100" aria-hidden="true">
                    {m.bar != null && (
                      <span className="absolute inset-y-0 w-1/2" style={neg ? { right: "50%" } : { left: "50%" }}>
                        <span className={`absolute inset-y-0 ${neg ? "right-0 bg-ink-500" : "left-0 bg-brand-500"}`} style={{ width: `${barPct(m.bar, max)}%` }} />
                      </span>
                    )}
                  </span>
                  <span className="w-32 shrink-0 text-right sm:w-40">
                    <span className={m.known ? `tabular-nums font-semibold ${neg ? "text-ink-700" : "text-emerald-700"}` : "text-xs text-ink-500"}>{m.text}</span>
                    {m.small && <small className="ml-1 tabular-nums text-[11px] text-ink-500">{m.small}</small>}
                  </span>
                </div>
                {notes.length > 0 && <p className="pl-1 text-[11px] text-ink-500">{notes.join(" · ")}</p>}
              </li>
            );
          })}
        </ul>
      )}
      {s.filter_note && <p className="text-xs text-ink-500">{s.filter_note}</p>}
    </Part>
  );
}

function HashtagPart({ s, brief }: { s: TrendReportSections["hashtags"]; brief: TrendReportBrief }) {
  const names = { ...(brief.competitor_names ?? {}), ...(s.account_names ?? {}) };
  return (
    <Part no={2} title="해시태그" part="hashtags" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">해시태그가 없어요.</p> : (
        <ol className="space-y-2">
          {s.rows.map((r, i) => (
            <li key={r.tag} id={evidenceDomId(`hashtag:${i + 1}`)} className="flex items-center gap-3 border border-ink-100 px-3 py-2 transition">
              <span className="w-5 shrink-0 text-center text-sm font-semibold tabular-nums text-ink-500">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-1.5">
                  <b className="break-all text-sm text-ink-900">#{r.tag.replace(/^#/, "")}</b>
                  {r.is_new && <span className="chip bg-brand-50 text-[11px] text-brand-700">새로 뜸</span>}
                </span>
                <span className="block break-words text-xs text-ink-500">{hashtagMeta(r, names)}</span>
              </span>
              <span className="shrink-0 text-right tabular-nums">
                <span className="block text-lg font-bold text-ink-900">{n(r.accounts)}곳</span>
                <span className="block text-[11px] text-ink-500">지난주 {n(r.previous_accounts)}곳</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {s.rows.length > 0 && <p className="text-xs text-ink-500">{HASHTAG_HIDDEN_NOTE}</p>}
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
            {s.posts.map((p, i) => <PostCard key={p.media_id} post={p} rank={i + 1} domId={evidenceDomId(`content:${i + 1}`)} />)}
          </ul>
        </section>
      )}
      {s.memes.length > 0 && (
        <section aria-label="같이 뜬 밈" className="space-y-1.5">
          <h4 className="text-sm font-semibold text-ink-900">같이 뜬 밈</h4>
          <ul className="space-y-1.5">
            {s.memes.map((m, i) => {
              const ad = AD[m.ad_material] ?? AD.unknown;
              return (
                <li key={m.name} id={evidenceDomId(`meme:${i + 1}`)} className="transition flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
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

/** 세로 썸네일 + 배수 배지 (누르면 새 탭) */
function BestPost({ r }: { r: TrendReportCompetitorRow }) {
  const p = r.best_post;
  if (!p) return <span className="flex h-28 w-20 shrink-0 items-center justify-center bg-ink-100 text-xs text-ink-500">-</span>;
  const link = safeLink(p.permalink);
  const src = thumbSrc(p.thumbnail_url);
  const body = (
    <span className="relative block h-28 w-20 shrink-0 overflow-hidden bg-ink-100">
      {src && <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" />}
      <span className="absolute bottom-1 right-1 bg-ink-900/80 px-1 py-0.5 text-[11px] font-bold leading-none text-white tabular-nums">×{p.ratio.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</span>
    </span>
  );
  return link ? (
    <a href={link} target="_blank" rel="noreferrer noopener" className="shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500" aria-label={`${r.account} 최고 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{body}</a>
  ) : body;
}

function CompetitorCard({ r, id, names }: { r: TrendReportCompetitorRow; id: string; names?: Record<string, string | null> }) {
  const chip = competitorChip(r.account, { ...names, ...(r.name ? { [r.account]: r.name } : {}) });
  const lines = traitLines(r);
  const num = (label: string, v: string) => (
    <div className="min-w-0"><dt className="text-[11px] text-ink-500">{label}</dt><dd className="text-sm font-semibold tabular-nums text-ink-900">{v}</dd></div>
  );
  return (
    <li id={id} className="flex min-w-0 gap-3 border border-ink-100 p-3 transition">
      <BestPost r={r} />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="truncate text-sm font-semibold text-ink-900">
          {chip.text}{chip.sub && <small className="ml-1 font-normal text-ink-500">{chip.sub}</small>}
        </p>
        {r.reason && <p className="text-xs text-ink-500">{r.reason}</p>}
        <dl className="grid grid-cols-3 gap-2">
          {num("주당 게시", r.posts_per_week != null ? r.posts_per_week.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "-")}
          {num("릴스 비율", shareText(r.reels_share) ?? "-")}
          {num("릴스 조회 중앙값", r.reels_view_median != null ? n(r.reels_view_median) : "-")}
        </dl>
        {lines.length > 0 && (
          <div>
            <p className="text-[11px] text-ink-500">잘 된 게시물은</p>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-700">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </div>
        )}
      </div>
    </li>
  );
}

function CompetitorPart({ s, brief }: { s: TrendReportSections["competitors"]; brief: TrendReportBrief }) {
  const summary = competitorSummary(s.rows);
  return (
    <Part no={4} title="경쟁사" part="competitors" status={s.status} reason={s.reason}>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">읽은 경쟁사 계정이 없어요.</p> : (
        <>
          {summary && <p className="text-sm font-semibold text-ink-900">{summary}</p>}
          <ul className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {sortCompetitors(s.rows).map((r) => (
              <CompetitorCard key={r.account} r={r} names={brief.competitor_names}
                id={evidenceDomId(`competitor:${s.rows.indexOf(r) + 1}`)} />
            ))}
          </ul>
        </>
      )}
      {s.accounts_failed.length > 0 && (
        <p className="text-xs text-ink-500">
          못 읽은 계정 · {s.accounts_failed.map((f) => `${competitorChip(f.account, brief.competitor_names).text} (${f.reason})`).join(" / ")}
        </p>
      )}
    </Part>
  );
}

/** 콘티 칸 하나: 시간 · 장면 · 접히는 "근거 N" (인쇄에서는 펼침) */
function BeatCell({ b, width }: { b: TrendReportBeat; width: number }) {
  const [open, setOpen] = useState(false);
  const items = beatEvidence(b);
  const printing = usePrinting();
  const shown = evidenceExpanded(open, printing);
  return (
    <li className="min-w-0 border border-ink-100 bg-surface px-3 py-2 sm:[flex-basis:var(--w)]" style={{ "--w": `${width}%` } as React.CSSProperties}>
      <p className="text-xs font-semibold text-ink-500">{BEAT_LABEL[b.slot] ?? b.slot}</p>
      <p className="mt-1 whitespace-pre-line break-words text-sm text-ink-900">{b.text}</p>
      {items.length > 0 && (
        <div className="mt-1.5">
          <button type="button" className="text-[11px] text-brand-700 hover:underline print-hide" aria-expanded={shown} onClick={() => setOpen(!open)}>
            근거 {items.length} {shown ? "▲" : "▼"}
          </button>
          <ul className={`mt-1 space-y-1 ${evidenceListClass(open)}`}>
            {items.map((e, i) => (
              <li key={i} className="text-[11px] text-ink-500">
                {e.id ? <button type="button" className="text-left hover:text-brand-700 hover:underline" onClick={() => jumpToEvidence(e.id!)}>{e.text}</button> : e.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/** 인쇄 중인지 (beforeprint~afterprint) */
function usePrinting(): boolean {
  const [p, setP] = useState(false);
  useEffect(() => {
    const on = () => setP(true), off = () => setP(false);
    window.addEventListener("beforeprint", on);
    window.addEventListener("afterprint", off);
    return () => { window.removeEventListener("beforeprint", on); window.removeEventListener("afterprint", off); };
  }, []);
  return p;
}

/** AI 추천: 위쪽 강조 카드. 결론 + 핵심 근거 칩 + 콘티 타임라인 */
function AiPick({ s, summaryReason }: { s: TrendReportSections["recommendation"]; summaryReason: string | null }) {
  const beats = sortBeats(s.beats);
  const widths = beatWidths(beats.map((b) => b.slot));
  const keys = keyEvidence(beats);
  const waiting = s.status === "waiting" || s.status === "running";
  return (
    <section className="card min-w-0 space-y-3 border-2 border-brand-500 p-4 sm:p-5 trend-report-section" aria-label="AI 추천">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip bg-brand-600 text-white">AI 추천</span>
        <span className="text-xs text-ink-500">AI가 아래 근거 숫자만 보고 쓴 방향이에요. 숫자는 코드가 계산해요.</span>
      </div>
      {s.conclusion ? (
        <p className="text-lg font-bold text-ink-900 sm:text-xl">{s.conclusion}</p>
      ) : (
        <p className="text-sm text-ink-500" aria-live="polite">
          {waiting ? "만드는 중이에요…" : summaryReason ?? s.reason ?? "AI 추천이 아직 없어요"}
        </p>
      )}
      {s.conclusion && keys.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="핵심 근거">
          {keys.map((k) => (
            <li key={k.id}>
              <button type="button" className="chip bg-ink-100 text-ink-700 hover:bg-brand-50 hover:text-brand-700" onClick={() => jumpToEvidence(k.id)}>{k.short}</button>
            </li>
          ))}
        </ul>
      )}
      {beats.length > 0 && (
        <ol className="flex flex-col gap-2 sm:flex-row sm:items-stretch" aria-label="콘티">
          {beats.map((b, i) => <BeatCell key={b.slot} b={b} width={widths[i]} />)}
        </ol>
      )}
      {s.music_note && <p className="text-[11px] text-ink-500">음악 · {s.music_note}</p>}
    </section>
  );
}

function printReport() {
  const body = document.body;
  body.classList.add(PRINT_CLASS);
  const done = () => { body.classList.remove(PRINT_CLASS); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}

export function TrendReportBody({ report, onRebuild }: { report: TrendReport; onRebuild?: (brief: TrendReportBrief) => void }) {
  const st = REPORT_STATUS[report.status];
  const sec = report.sections;
  const groups = limitGroups(report);
  return (
    <article className="trend-report-print space-y-4" aria-label="트렌드 분석 보고서">
      <header className="card space-y-3 p-4 sm:p-5 trend-report-section">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-bold text-ink-900">트렌드 분석 보고서{report.brief.product ? ` · ${report.brief.product}` : ""}</h2>
          <StatusChip label={st.label} t={st.tone} />
          <span className="text-xs text-ink-500">
            <span className="font-semibold tabular-nums text-ink-700">{reportLabel(report.report_id)}</span> · {when(report.created_at)} 의뢰 · {CALLER_LABEL[report.caller] ?? report.caller}
            {report.finished_at && ` · ${when(report.finished_at)} 끝남`}
          </span>
          <span className="ml-auto flex flex-wrap gap-2 print-hide">
            {onRebuild && <button type="button" className="btn-ghost" onClick={() => onRebuild(report.brief)}>의뢰 고쳐서 다시 만들기</button>}
            <button type="button" className="btn-primary" onClick={printReport}>PDF로 저장</button>
            <button type="button" className="btn-ghost" disabled title="다음 단계">기획으로 넘기기</button>
          </span>
        </div>
        {keepPolling(report.status) && <Progress report={report} />}
        <BriefList brief={report.brief} />
      </header>

      <AiPick s={sec.recommendation} summaryReason={report.summary_reason} />

      <div className="flex flex-wrap items-center gap-2 pt-2">
        <h3 className="text-base font-bold text-ink-900">근거 데이터</h3>
        <EvidenceChip kind="api" /><EvidenceChip kind="ai" /><EvidenceChip kind="human" />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SearchPart s={sec.search} brief={report.brief} />
        <HashtagPart s={sec.hashtags} brief={report.brief} />
      </div>
      <ContentPart s={sec.content} />
      <CompetitorPart s={sec.competitors} brief={report.brief} />
      <section className="card space-y-1 border-dashed p-4 opacity-70 print-hide" aria-label="다음 단계">
        <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink-700">영상 안쪽 분석 · 유료 광고 — 담당자 업로드 <PreparingChip /></p>
        <p className="text-xs text-ink-500">다음 단계에서 담당자가 올린 영상·광고 자료로 채웁니다.</p>
      </section>

      <section className="card space-y-3 p-4 sm:p-5 trend-report-section" aria-label="근거와 한계">
        <h3 className="text-base font-semibold text-ink-900">근거와 한계</h3>
        {groups ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {groups.map((g) => (
              <div key={g.label} className={`min-w-0 space-y-1 border px-3 py-2 ${g.cls}`}>
                <p className="text-xs font-semibold">{g.label}</p>
                <ul className="list-disc space-y-1 pl-4 text-xs">{g.items.map((l, i) => <li key={i}>{l}</li>)}</ul>
              </div>
            ))}
          </div>
        ) : report.limits.length > 0 ? (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {report.limits.map((l, i) => <li key={i} className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-ink-700">{l}</li>)}
          </ul>
        ) : <p className="text-sm text-ink-500">적힌 한계가 없어요.</p>}
        <p className="text-xs text-ink-500 tabular-nums">호출 · 인스타 {n(report.calls.instagram)}번 · 네이버 {n(report.calls.naver)}번 · AI {n(report.calls.llm)}번</p>
      </section>
    </article>
  );
}

/** 보고서 id로 읽어 그린다. 서버에 skill이 아직 없으면(403) "준비 중".
 *  autoPrint: 대화 완료 카드의 "PDF로 저장"으로 열었으면 보고서를 그린 뒤 한 번 인쇄하고 onPrinted */
export function TrendReportView({ id, onRebuild, autoPrint = false, onPrinted }: {
  id: string; onRebuild?: (brief: TrendReportBrief) => void; autoPrint?: boolean; onPrinted?: () => void;
}) {
  const { load, reload } = useReport(id);
  const ready = load.kind === "ok";
  useEffect(() => {
    if (!autoPrint || !ready) return;
    // 그린 다음 프레임에 인쇄 (보고서가 화면에 들어간 뒤)
    const t = setTimeout(() => { onPrinted?.(); printReport(); }, 0);
    return () => clearTimeout(t);
  }, [autoPrint, ready, onPrinted]);
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
  return <TrendReportBody report={load.report} onRebuild={onRebuild} />;
}
