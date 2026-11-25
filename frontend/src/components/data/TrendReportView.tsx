import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { TONE, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { when } from "@/components/data/Sources";
import { AD, MegaRow, times } from "@/components/data/TrendMemes";
import { perSubscriberLabel } from "@/lib/memeShorts";
import { safeLink } from "@/lib/safeLink";
import { printWithClass, usePrinting } from "@/lib/print";
import { waitForImages } from "@/lib/printImages";
import { COMPACT, METRIC, ago } from "@/components/data/InstagramRadar";
import {
  dataAi, type InstagramRadarPost, type SectionStatus, type TrendReport, type TrendReportBeat, type TrendReportBrief, type TrendReportCompetitorRow,
  type TrendReportHandoffOutput, type TrendReportMaterial, type TrendReportSections, type TrendThemeSection,
} from "@/lib/data";
import { useCompany } from "@/demo/company";
import { DeliveryBadge, HandoffButton, ReportInsightCard, usePlanningRecord } from "@/components/data/ReportInsights";
import { deliveryOf, handoffChip, proposalFor, reportInsightId } from "@/lib/handoffView";
import { kindLabel, thumbSrc } from "@/lib/igRadarGrid";
import { planDocPath } from "@/lib/planDoc";
import { sectionState, type SectionState } from "@/lib/segmentView";
import {
  BEAT_LABEL, CALLER_LABEL, EVIDENCE, HASHTAG_HIDDEN_NOTE, MATERIAL_LABEL, MATERIALS_NOTE, REPORT_POLL_MS, REPORT_STATUS, SECTION_EVIDENCE, SECTION_STATUS,
  aiPickMode, beatEvidence, beatWidths, competitorChip, competitorSummary, countedSections, evidenceDomId, evidenceExpanded, evidenceListClass, hashtagMeta,
  headerRows, keepPolling, keyEvidence, limitGroups, printHeaderLine, recommendationItems, reportLabel, sectionNotes, SECTION_TITLE, shareText, showThemeSection,
  sortBeats, sortCompetitors, themeRowView, traitLines, type ChipTone, type EvidenceKind, type ReportPart,
} from "@/lib/trendReportView";
import {
  briefLine, defaultOpenSections, isOpen, keyNumbers, searchTable, sectionOfEvidence, sectionSummary, type FoldKey,
} from "@/lib/trendReportRead";

// 트렌드 분석 의뢰 보고서 하나 (trend_report_get). 주소 #request?id=<report_id> (분석 의뢰 탭, TrendRequest.tsx가 연다).
// - 진행 중(queued·running)이면 섹션별 진행 칩을 보여 주고 5초마다 다시 읽는다. 완료·부분·실패·중단이면 멈춘다
// - 11/23 한눈에 읽히게 (계산은 lib/trendReportRead.ts):
//   머리 = 제목·상태·번호 + [PDF로 저장]·[기획서 보기](기획에 넘긴 제안이 있을 때만) + 의뢰 한 줄 요약("의뢰 내용 자세히"로 표 펼침)
//   → 결론 카드(AI 추천 결론 크게 · 핵심 숫자 3칸 · 기획에 넘길 재료(materials, #1597. 재료 없는 옛 보고서는 콘티))
//   → 근거 섹션(0 주제 후보 · 1 검색 · 2 해시태그 · 3 콘텐츠 · 4 경쟁사 · 근거와 한계)은 접는 카드 + 머리 한 줄 요약.
//     기본 펼침은 결론이 기대는 근거의 섹션만, 누른 것은 이 화면에서만 기억한다. 핵심 숫자·근거를 누르면 그 섹션을 펴고 그 행으로 간다
//   검색 = 표 하나(값 없는 키워드는 한 줄로), 콘텐츠 = 작은 썸네일(54×72) 가로 카드 3열, 경쟁사 = 표 하나
// - 머리 "기획에 보내기"(trend_report_handoff)와 맨 아래 카드는 결과를 같이 쓴다. 기획 기록에 이미 있으면 버튼 대신 [기획서 보기]
// - 인쇄(PDF): 맨 위에 인쇄 때만 머리글 한 줄("TR-xxxx · 회사 · 만든 시각 · 상태"). 접힌 섹션·의뢰 표·근거 목록은 모두 펼친다(hidden print:block).
//   인쇄 직전에 보고서 안 이미지를 eager로 받고(최대 3초, lib/printImages.ts) 못 받은 것은 숨긴다. 게시물 3열·썸네일 고정·콘티 2×2 (styles.css)
// - 근거 id("search:1")는 근거 데이터 행의 DOM id(tr-ev-search-1)로 이어진다
// - 섹션 status가 known이 아니면 내용 대신 서버 이유(대기·진행이면 진행 표시)
// - 숫자는 서버 값 그대로. 외부 글(캡션·밈 설명)은 텍스트로만, 링크는 safeLink 통과한 것만
// - "PDF로 저장" = window.print(). 인쇄할 때는 이 보고서만 남기고(styles.css .trend-report-print) 버튼·메뉴를 숨긴다

const tone = (t: ChipTone) => TONE[t];
const n = (v: number) => v.toLocaleString("ko-KR");
const fix1 = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
const PRINT_CLASS = "print-trend-report";
/** 접힌 칸: 화면에서는 숨기고 인쇄에서는 펼친다 */
const foldClass = (open: boolean) => (open ? "" : "hidden print:block");

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

/** 근거 행으로 스크롤하고 잠깐 강조 */
function jumpToEvidence(id: string) {
  const el = document.getElementById(evidenceDomId(id));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const cls = ["ring-2", "ring-brand-500", "bg-brand-50"];
  el.classList.add(...cls);
  setTimeout(() => el.classList.remove(...cls), 1800);
}

/** 접는 섹션: 머리(번호·제목·근거 칩·상태·한 줄 요약·펼침 표시)를 누르면 펼침. known이면 내용, 아니면 서버 이유 */
function Fold({ no, title, part, status, reason, summary, open, onToggle, children }: {
  no?: number; title: string; part?: ReportPart; status?: SectionStatus; reason?: string | null; summary: string | null;
  open: boolean; onToggle: () => void; children: ReactNode;
}) {
  const s = status ? SECTION_STATUS[status] : null;
  return (
    <section className="card min-w-0 trend-report-section" aria-label={title}>
      <h3>
        <button type="button" aria-expanded={open} onClick={onToggle}
          className="flex w-full flex-wrap items-center gap-x-2.5 gap-y-1 px-4 py-3 text-left hover:bg-ink-50 sm:px-5">
          <span className="text-base font-semibold text-ink-900">{no != null && <span className="mr-1.5 text-ink-400">{no}</span>}{title}</span>
          {part && <EvidenceChip kind={SECTION_EVIDENCE[part]} />}
          {s && status !== "known" && <StatusChip label={s.label} t={s.tone} />}
          {summary && <span className="min-w-0 break-words text-[12.5px] font-normal text-ink-500">{summary}</span>}
          <span className={`ml-auto text-ink-400 transition print-hide ${open ? "rotate-90" : ""}`} aria-hidden="true">›</span>
        </button>
      </h3>
      <div className={`trend-fold-body space-y-3 px-4 pb-4 sm:px-5 sm:pb-5 ${foldClass(open)}`}>
        {!status || status === "known" ? children
          : status === "waiting" || status === "running" ? (
            <p className="text-sm text-ink-500" aria-live="polite">{status === "running" ? "만드는 중이에요…" : "앞 단계를 기다리는 중이에요."}</p>
          ) : (
            <p className="text-sm text-ink-500">{reason ?? "이유를 받지 못했어요"}</p>
          )}
      </div>
    </section>
  );
}

/** 섹션별 진행 칩 (주제 후보는 보일 때만, lib/trendReportView countedSections) */
function Progress({ report }: { report: TrendReport }) {
  return (
    <ul className="flex flex-wrap gap-2 print-hide" aria-label="섹션별 진행">
      {countedSections(report).map((k) => {
        const s = SECTION_STATUS[report.sections[k]?.status ?? "waiting"];
        return <li key={k}><span className={`chip text-xs ${tone(s.tone)}`}>{SECTION_TITLE[k]} · {s.label}</span></li>;
      })}
    </ul>
  );
}

/** 끝난 보고서: 일부 완료 이유(확인 못 한 칸)와 접힌 "확인했지만 없던 칸" (empty는 완료로 셈) */
function SectionNotes({ report }: { report: TrendReport }) {
  const { missing, empty } = sectionNotes(report);
  const partial = report.status === "partial" || report.status === "failed";
  if (!(partial && missing.length) && !empty.length) return null;
  return (
    <div className="space-y-2 text-sm">
      {partial && missing.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-ink-700">확인 못 한 칸</p>
          <ul className="mt-1 space-y-0.5 text-ink-500">
            {missing.map((m) => <li key={m.key}><span className="font-medium text-ink-700">{m.title}</span> · {m.reason}</li>)}
          </ul>
        </div>
      )}
      {empty.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-semibold text-ink-500">확인했지만 없던 칸 {empty.length}개 (완료로 셈)</summary>
          <ul className="mt-1 space-y-0.5 text-ink-500">
            {empty.map((m) => <li key={m.key}><span className="font-medium text-ink-700">{m.title}</span> · {SECTION_STATUS.empty.label} · {m.reason}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function SourceTag({ text }: { text: string | null }) {
  return text ? <span className="chip bg-ink-100 text-[11px] text-ink-500">{text}</span> : null;
}

/** 의뢰 표: 항목 | 칩 목록. 주제가 있으면 "주제" 줄(값 + 쓰임 칩), 주제 후보에서 온 키워드는 앞에 ★ */
function BriefList({ brief: b, theme }: { brief: TrendReportBrief; theme?: TrendThemeSection }) {
  return (
    <dl className="space-y-2">
      {headerRows(b, theme).map((row) => (
        <div key={row.key} className="print-keep-grid grid grid-cols-[5.5rem_minmax(0,1fr)] items-start gap-x-3 gap-y-1">
          <dt className="pt-0.5 text-xs text-ink-500">{row.label}</dt>
          <dd className="flex min-w-0 flex-wrap items-center gap-1.5">
            {row.key === "product" ? (
              <>
                <span className="break-words text-sm font-semibold text-ink-900">{b.product ?? "정하지 않음"}</span>
                {b.goal && <span className="chip bg-brand-50 text-brand-700">목표 · {b.goal}</span>}
              </>
            ) : row.chips.length === 0 ? <span className="text-sm text-ink-500">없음</span>
              : row.chips.map((c, i) => (
                <span key={i} className={`chip ${row.key === "theme" && i > 0 ? "bg-brand-50 text-brand-700" : "bg-ink-100 text-ink-900"}`}>
                  {c.star && <span className="text-[10px] text-brand-600" aria-label="주제 후보에서 온 키워드">★</span>}
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

/** 의뢰 한 줄 요약 + "의뢰 내용 자세히"(지금 표, 인쇄 때 펼침) */
function BriefSummary({ brief, theme }: { brief: TrendReportBrief; theme?: TrendThemeSection }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[12.5px] text-ink-500">
        {briefLine(brief).map((x) => <span key={x.label}>{x.label} <b className="font-semibold text-ink-900">{x.value}</b></span>)}
        <button type="button" className="text-brand-700 underline-offset-2 hover:underline print-hide" aria-expanded={open} onClick={() => setOpen(!open)}>
          의뢰 내용 자세히 {open ? "▲" : "▼"}
        </button>
      </p>
      <div className={foldClass(open)}><BriefList brief={brief} theme={theme} /></div>
    </div>
  );
}

/** 0 주제 후보 (#1540): 후보 · 관심 막대(0~100) · 최근 7일 · 함께 본 곳 · 이름이 든 우리 상품. 버린 후보는 접기(인쇄 때 펼침) */
function ThemeBody({ s }: { s: TrendThemeSection }) {
  const [open, setOpen] = useState(false);
  const printing = usePrinting();
  const shown = evidenceExpanded(open, printing);
  return (
    <>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">데이터랩에서 값이 나온 후보가 없어요.</p> : (
        <div className="overflow-x-auto trend-print-table">
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-500">
                <th className="py-1 pr-2 font-medium">후보</th>
                <th className="py-1 pr-2 font-medium">검색 관심 (최대 100)</th>
                <th className="py-1 pr-2 text-right font-medium">최근 7일</th>
                <th className="py-1 pr-2 font-medium">함께 본 곳</th>
                <th className="py-1 font-medium">이름이 든 우리 상품</th>
              </tr>
            </thead>
            <tbody>
              {s.rows.map((r, i) => {
                const v = themeRowView(r);
                return (
                  <tr key={r.name} id={evidenceDomId(`theme:${i + 1}`)} className="border-t border-ink-100 align-top transition">
                    <td className="py-1.5 pr-2 font-medium text-ink-900">{v.name}</td>
                    <td className="py-1.5 pr-2">
                      <span className="flex items-center gap-2">
                        <span className="relative h-2 w-28 shrink-0 bg-ink-100" aria-hidden="true">
                          <span className="absolute inset-y-0 left-0 bg-brand-500" style={{ width: `${v.level}%` }} />
                        </span>
                        <span className="tabular-nums text-ink-700">{v.level}</span>
                      </span>
                    </td>
                    <td className={`py-1.5 pr-2 text-right tabular-nums ${v.change.startsWith("+") ? "text-emerald-700" : "text-ink-700"}`}>{v.change}</td>
                    <td className="py-1.5 pr-2">
                      <span className="flex flex-wrap gap-1">{v.seenIn.map((x) => <span key={x} className="chip bg-ink-100 text-[11px] text-ink-700">{x}</span>)}</span>
                    </td>
                    <td className="py-1.5 text-xs text-ink-700">{v.products}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {s.method && <p className="text-xs text-ink-500">{s.method}</p>}
      {s.rows[0]?.basis && <p className="text-xs text-ink-500">{s.rows[0].basis}</p>}
      {s.dropped.length > 0 && (
        <div>
          <button type="button" className="text-xs text-brand-700 hover:underline print-hide" aria-expanded={shown} onClick={() => setOpen(!open)}>
            버린 후보 {s.dropped.length}개 {shown ? "▲" : "▼"}
          </button>
          <ul className={`mt-1 space-y-0.5 text-xs text-ink-500 ${evidenceListClass(open)}`}>
            {s.dropped.map((d) => <li key={d.name}><b className="font-medium text-ink-700">{d.name}</b> · {d.reason}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

/** 1 검색: 표 하나 (키워드 · 검색 7일 · 쇼핑 클릭 · 메모). 값 없는 키워드는 한 줄로 */
function SearchBody({ s }: { s: TrendReportSections["search"] }) {
  if (s.rows.length === 0) return <p className="text-sm text-ink-500">볼 키워드가 없어요.</p>;
  return (
    <>
      <div className="overflow-x-auto trend-print-table">
        <table className="w-full min-w-[520px] text-[13px]">
          <thead>
            <tr className="text-left text-xs text-ink-500">
              <th className="py-1 pr-2 font-medium">키워드</th>
              <th className="py-1 pr-2 text-right font-medium">검색 7일</th>
              <th className="py-1 pr-2 text-right font-medium">쇼핑 클릭</th>
              <th className="py-1 font-medium">메모</th>
            </tr>
          </thead>
          <tbody>
            {searchTable(s.rows).map((r) => (
              <tr key={r.key} id={r.no != null ? evidenceDomId(`search:${r.no}`) : undefined} className="border-t border-ink-100 align-top transition">
                <td className="py-1.5 pr-2 font-medium text-ink-900">{r.keyword}</td>
                <td className={`py-1.5 pr-2 text-right tabular-nums ${r.up ? "font-semibold text-emerald-700" : "text-ink-700"}`}>{r.search}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-ink-700">{r.shopping}</td>
                <td className="py-1.5 text-xs text-ink-500">{r.memo}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.filter_note && <p className="text-xs text-ink-500">{s.filter_note}</p>}
    </>
  );
}

function HashtagBody({ s, brief }: { s: TrendReportSections["hashtags"]; brief: TrendReportBrief }) {
  const names = { ...(brief.competitor_names ?? {}), ...(s.account_names ?? {}) };
  if (s.rows.length === 0) return <p className="text-sm text-ink-500">해시태그가 없어요.</p>;
  return (
    <>
      <ol className="space-y-1.5">
        {s.rows.map((r, i) => (
          <li key={r.tag} id={evidenceDomId(`hashtag:${i + 1}`)} className="flex items-center gap-3 border border-ink-100 px-3 py-1.5 transition">
            <span className="w-5 shrink-0 text-center text-sm font-semibold tabular-nums text-ink-500">{i + 1}</span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-1.5">
                <b className="break-all text-sm text-ink-900">#{r.tag.replace(/^#/, "")}</b>
                {r.is_new && <span className="chip bg-brand-50 text-[11px] text-brand-700">새로 뜸</span>}
              </span>
              <span className="block break-words text-xs text-ink-500">{hashtagMeta(r, names)}</span>
            </span>
            <span className="shrink-0 text-right tabular-nums">
              <span className="block text-base font-bold text-ink-900">{n(r.accounts)}곳</span>
              <span className="block text-[11px] text-ink-500">지난주 {n(r.previous_accounts)}곳</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-ink-500">{HASHTAG_HIDDEN_NOTE}</p>
    </>
  );
}

/** 보고서용 작은 게시물 카드: 썸네일 54×72 고정 + 글 (레이더 화면의 PostCard는 그대로). 이미지를 못 받으면 회색 칸 + 형식 글자 */
function ReportPost({ p, rank, domId }: { p: InstagramRadarPost; rank: number; domId: string }) {
  const [broken, setBroken] = useState(false);
  const link = safeLink(p.permalink);
  const src = thumbSrc(p.thumbnail_url);
  const kind = kindLabel(p);
  const posted = ago(p.posted_at);
  const body = (
    <>
      <span className="relative block h-[72px] w-[54px] shrink-0 overflow-hidden bg-ink-100">
        {src && !broken ? (
          <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" onError={() => setBroken(true)} />
        ) : (
          <span className="absolute inset-0 grid place-items-center text-[11px] font-medium text-ink-500">{kind ?? "게시물"}</span>
        )}
        <span className="absolute bottom-0.5 right-0.5 bg-ink-900/80 px-1 py-0.5 text-[10px] font-bold leading-none text-white tabular-nums">×{fix1(p.ratio)}</span>
      </span>
      <span className="flex min-w-0 flex-col gap-0.5 text-xs leading-snug">
        <b className="truncate text-[13px] text-ink-900"><span className="mr-1 font-normal text-ink-400">{rank}</span>{p.account}</b>
        <span className="text-ink-700">{[kind, `${METRIC[p.metric]} ${COMPACT.format(p.value)}`, posted].filter(Boolean).join(" · ")}</span>
        {p.standout && <span className="font-medium text-emerald-700">{p.standout}</span>}
        {p.ad_excluded && <span className="font-medium text-ink-500">{p.ad_excluded}</span>}
      </span>
    </>
  );
  const cls = "flex min-w-0 gap-2 border border-ink-100 p-1.5";
  return (
    <li id={domId} className="min-w-0 transition">
      {link ? (
        <a href={link} target="_blank" rel="noreferrer noopener" className={`${cls} hover:bg-ink-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500`}
          aria-label={`${rank}위 ${p.account} 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{body}</a>
      ) : <div className={cls}>{body}</div>}
    </li>
  );
}

function ContentBody({ s }: { s: TrendReportSections["content"] }) {
  return (
    <>
      {(s.audience_line || s.audience_widened || s.audience_thin) && (
        <div className="space-y-1">
          {s.audience_line && <p className="text-sm font-medium text-ink-900">{s.audience_line}</p>}
          {s.audience_widened && !s.audience_line?.includes("넓혀") && <p className="text-xs text-ink-500">대상층에 맞는 계정이 적어 전체 계정으로 넓혀 봤어요</p>}
          {s.audience_thin && <span className={`chip text-[11px] ${TONE.warn}`}>인스타 표본 적음 · 검색·유튜브 근거를 앞에 뒀어요</span>}
        </div>
      )}
      {s.posts.length === 0 ? <p className="text-sm text-ink-500">갑자기 뜬 게시물이 없어요.</p> : (
        <section aria-label="갑자기 뜬 게시물">
          <h4 className="mb-1.5 text-sm font-semibold text-ink-900">갑자기 뜬 게시물 <small className="font-normal text-ink-500">평소 대비 배수 · {n(s.posts.length)}개</small></h4>
          <ul className="print-keep-grid trend-print-posts grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {s.posts.map((p, i) => <ReportPost key={p.media_id} p={p} rank={i + 1} domId={evidenceDomId(`content:${i + 1}`)} />)}
          </ul>
        </section>
      )}
      {(s.mega?.length ?? 0) > 0 && (
        <section aria-label="조회 천만 이상" className="space-y-1.5">
          <h4 className="text-sm font-semibold text-ink-900">조회 천만 이상 <small className="font-normal text-ink-500">조회수 순 · {n(s.mega!.length)}개</small></h4>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">{s.mega!.map((x) => <MegaRow key={`${x.platform}:${x.item_id}`} x={x} />)}</ul>
        </section>
      )}
      {(s.shorts?.length ?? 0) > 0 && (
        <section aria-label="쇼츠 검색 근거" className="space-y-1.5">
          <h4 className="text-sm font-semibold text-ink-900">쇼츠 검색 근거 <small className="font-normal text-ink-500">구독자 대비 조회수 순 · {n(s.shorts!.length)}개</small></h4>
          <ul className="space-y-1.5">
            {s.shorts!.map((v) => {
              const link = safeLink(v.video_url);
              const rel = perSubscriberLabel(v);
              const title = v.title || v.video_id;
              return (
                <li key={v.video_id} className="text-sm">
                  {link ? <a href={link} target="_blank" rel="noreferrer noopener" className="font-medium text-brand-700 underline-offset-2 hover:underline">{title}</a> : <b className="font-medium text-ink-900">{title}</b>}
                  <small className="block text-xs text-ink-500">
                    {[v.channel_title, v.views !== null ? `조회 ${v.views.toLocaleString("ko-KR")}` : null, rel, v.ad_excluded].filter(Boolean).join(" · ")}
                  </small>
                </li>
              );
            })}
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
    </>
  );
}

/** 최고 게시물 배수 칸 (링크면 새 탭) */
function BestRatio({ r }: { r: TrendReportCompetitorRow }) {
  const p = r.best_post;
  if (!p) return <>–</>;
  const link = safeLink(p.permalink);
  const text = `×${fix1(p.ratio)}${p.media_product_type === "REELS" ? " 릴스" : ""}`;
  return link ? (
    <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline"
      aria-label={`${r.account} 최고 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{text}</a>
  ) : <>{text}</>;
}

/** 4 경쟁사: 표 하나 (계정 · 주당 게시 · 릴스 비율 · 릴스 조회 중앙값 · 최고 게시물 배수 · 잘 된 게시물은). 정렬은 sortCompetitors */
function CompetitorBody({ s, brief }: { s: TrendReportSections["competitors"]; brief: TrendReportBrief }) {
  const summary = competitorSummary(s.rows);
  return (
    <>
      {s.rows.length === 0 ? <p className="text-sm text-ink-500">읽은 경쟁사 계정이 없어요.</p> : (
        <>
          {summary && <p className="text-sm font-semibold text-ink-900">{summary}</p>}
          <div className="overflow-x-auto trend-print-table trend-print-shrink">
            <table className="w-full min-w-[680px] text-[13px]">
              <thead>
                <tr className="text-left text-xs text-ink-500">
                  <th className="py-1 pr-2 font-medium">계정</th>
                  <th className="py-1 pr-2 text-right font-medium">주당 게시</th>
                  <th className="py-1 pr-2 text-right font-medium">릴스 비율</th>
                  <th className="py-1 pr-2 text-right font-medium">릴스 조회 중앙값</th>
                  <th className="py-1 pr-2 text-right font-medium">최고 게시물</th>
                  <th className="py-1 font-medium">잘 된 게시물은</th>
                </tr>
              </thead>
              <tbody>
                {sortCompetitors(s.rows).map((r) => {
                  const chip = competitorChip(r.account, { ...brief.competitor_names, ...(r.name ? { [r.account]: r.name } : {}) });
                  const lines = traitLines(r);
                  return (
                    <tr key={r.account} id={evidenceDomId(`competitor:${s.rows.indexOf(r) + 1}`)} className="border-t border-ink-100 align-top transition">
                      <td className="py-1.5 pr-2">
                        <b className="font-semibold text-ink-900">{chip.text}</b>{chip.sub && <small className="ml-1 text-ink-500">{chip.sub}</small>}
                        {r.reason && <span className="block text-xs text-ink-500">{r.reason}</span>}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{r.posts_per_week != null ? fix1(r.posts_per_week) : "–"}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{shareText(r.reels_share) ?? "–"}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{r.reels_view_median != null ? n(r.reels_view_median) : "–"}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums"><BestRatio r={r} /></td>
                      <td className="py-1.5 text-xs text-ink-700">{lines.length ? lines.join(" · ") : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {s.accounts_failed.length > 0 && (
        <p className="text-xs text-ink-500">
          못 읽은 계정 · {s.accounts_failed.map((f) => `${competitorChip(f.account, brief.competitor_names).text} (${f.reason})`).join(" / ")}
        </p>
      )}
    </>
  );
}

/** 근거 목록 (콘티 칸·재료 칸 공용): 접히는 "근거 N" (인쇄에서는 펼침). id가 있으면 누르면 그 행으로 */
function EvidenceList({ items, onJump }: { items: { id: string | null; text: string }[]; onJump: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const printing = usePrinting();
  const shown = evidenceExpanded(open, printing);
  if (!items.length) return null;
  return (
    <div className="mt-1.5">
      <button type="button" className="text-[11px] text-brand-700 hover:underline print-hide" aria-expanded={shown} onClick={() => setOpen(!open)}>
        근거 {items.length} {shown ? "▲" : "▼"}
      </button>
      <ul className={`mt-1 space-y-1 ${evidenceListClass(open)}`}>
        {items.map((e, i) => (
          <li key={i} className="text-[11px] text-ink-500">
            {e.id ? <button type="button" className="text-left hover:text-brand-700 hover:underline" onClick={() => onJump(e.id!)}>{e.text}</button> : e.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 콘티 칸 하나: 시간 · 장면 · 근거 */
function BeatCell({ b, width, onJump }: { b: TrendReportBeat; width: number; onJump: (id: string) => void }) {
  return (
    <li className="min-w-0 border border-ink-100 bg-surface px-3 py-2 sm:[flex-basis:var(--w)]" style={{ "--w": `${width}%` } as React.CSSProperties}>
      <p className="text-xs font-semibold text-ink-500">{BEAT_LABEL[b.slot] ?? b.slot}</p>
      <p className="mt-1 whitespace-pre-line break-words text-sm text-ink-900">{b.text}</p>
      <EvidenceList items={beatEvidence(b)} onJump={onJump} />
    </li>
  );
}

/** 기획에 넘길 재료 한 칸 (#1597 계약 TrendReportMaterial): 종류 한국어 이름(MATERIAL_LABEL) · 무엇을 쓰라는지 · 근거 */
function MaterialItem({ m, onJump }: { m: TrendReportMaterial; onJump: (id: string) => void }) {
  return (
    <li className="min-w-0 border border-ink-100 bg-surface px-3 py-2">
      <p className="text-xs font-semibold text-ink-500">{MATERIAL_LABEL[m.kind]}</p>
      <p className="mt-1 whitespace-pre-line break-words text-sm text-ink-900">{m.text}</p>
      <EvidenceList items={beatEvidence(m)} onJump={onJump} />
    </li>
  );
}

/** 결론 카드: AI 추천 결론 한 줄 크게 · 핵심 숫자 3칸 · 기획에 넘길 재료 (재료 없는 옛 보고서는 콘티. 재료 칸은 여기 한 곳에만) */
function Conclusion({ s, summaryReason, onJump }: { s: TrendReportSections["recommendation"]; summaryReason: string | null; onJump: (id: string) => void }) {
  const mode = aiPickMode(s);
  const beats = mode === "beats" ? sortBeats(s.beats) : [];
  const widths = beatWidths(beats.map((b) => b.slot));
  const keys = keyNumbers(keyEvidence(recommendationItems(s)));
  const waiting = s.status === "waiting" || s.status === "running";
  return (
    <section className="card min-w-0 space-y-3 border-2 border-brand-500 p-4 sm:p-5 trend-report-section" aria-label="결론">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip bg-brand-600 text-white">결론 · AI 추천</span>
        <span className="text-xs text-ink-500">AI가 아래 근거 숫자만 보고 쓴 방향이에요. 숫자는 코드가 계산해요.</span>
      </div>
      {s.conclusion ? (
        <p className="text-lg font-bold leading-snug text-ink-900 sm:text-xl">{s.conclusion}</p>
      ) : (
        <p className="text-sm text-ink-500" aria-live="polite">
          {waiting ? "만드는 중이에요…" : summaryReason ?? s.reason ?? "AI 추천이 아직 없어요"}
        </p>
      )}
      {s.conclusion && keys.length > 0 && (
        <ul className="print-keep-grid grid grid-cols-1 gap-2 sm:grid-cols-3" aria-label="핵심 숫자">
          {keys.map((k) => (
            <li key={k.id} className="min-w-0">
              <button type="button" title={k.text} onClick={() => onJump(k.id)}
                className="block h-full w-full border border-ink-100 px-3 py-2 text-left hover:border-brand-500 hover:bg-brand-50">
                <b className="block break-words text-lg font-bold tabular-nums text-ink-900">{k.big}</b>
                {k.label && <span className="block break-words text-xs text-ink-500">{k.label}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {mode === "materials" ? (
        <div className="space-y-1.5">
          <p className="text-[13px] font-semibold text-ink-900">기획에 넘길 재료 <span className="font-normal text-ink-500">· {MATERIALS_NOTE}</span></p>
          <ol className="trend-print-beats print-keep-grid grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="기획에 넘길 재료">
            {(s.materials ?? []).map((m, i) => <MaterialItem key={`${m.kind}:${i}`} m={m} onJump={onJump} />)}
          </ol>
        </div>
      ) : beats.length > 0 && (
        <ol className="trend-print-beats flex flex-col gap-2 sm:flex-row sm:items-stretch" aria-label="콘티">
          {beats.map((b, i) => <BeatCell key={b.slot} b={b} width={widths[i]} onJump={onJump} />)}
        </ol>
      )}
      {s.music_note && <p className="text-[11px] text-ink-500">음악 · {s.music_note}</p>}
    </section>
  );
}

/** 인쇄 직전: 보고서 안 이미지를 eager로 받고(최대 3초) 못 받은 것은 숨긴 뒤 인쇄 */
async function printReport() {
  const root = document.querySelector(".trend-report-print");
  const imgs = root ? Array.from(root.querySelectorAll("img")) : [];
  await waitForImages(imgs);
  printWithClass(PRINT_CLASS);
}

/** readOnly(11/26 대화방 카드 안 펼쳐 보기): 머리 버튼(의뢰 고치기·PDF·기획에 보내기)과 맨 아래 다음 단계 카드를 두지 않는다 */
export function TrendReportBody({ report, onRebuild, readOnly = false }: { report: TrendReport; onRebuild?: (brief: TrendReportBrief) => void; readOnly?: boolean }) {
  const navigate = useNavigate();
  const st = REPORT_STATUS[report.status];
  const sec = report.sections;
  const groups = limitGroups(report);
  const company = useCompany();
  // 기획에 보내기: 머리 버튼과 맨 아래 인사이트 카드가 결과를 같이 쓴다. 기획 기록에 이미 있으면 버튼 대신 [기획서 보기]
  const planning = usePlanningRecord();
  const [sent, setSent] = useState<TrendReportHandoffOutput | null>(null);
  const proposal = proposalFor(planning, reportInsightId(report.report_id));
  const delivery = sent && !proposal ? handoffChip(sent) : deliveryOf(proposal, null);
  const finished = report.status === "done" || report.status === "partial";
  const sentId = sent && (sent.outcome === "created" || sent.outcome === "duplicate") ? sent.proposal_id : null;
  const planPath = proposal ? planDocPath({ id: proposal.id, spec: proposal.spec }) : sentId ? planDocPath({ id: sentId }) : null;

  // 근거 섹션 접기: 기본 = 결론이 기대는 근거의 섹션만 펼침. 누른 값은 이 화면에서만 기억한다
  const defaults = useMemo(() => defaultOpenSections(sec.recommendation), [sec.recommendation]);
  const [picked, setPicked] = useState<Partial<Record<FoldKey, boolean>>>({});
  const open = (k: FoldKey) => isOpen(k, defaults, picked);
  const toggle = (k: FoldKey) => setPicked((p) => ({ ...p, [k]: !isOpen(k, defaults, p) }));
  /** 핵심 숫자·근거를 누르면 그 섹션을 펴고 그 행으로 */
  const jump = (id: string) => {
    const k = sectionOfEvidence(id);
    if (k && !open(k)) setPicked((p) => ({ ...p, [k]: true }));
    requestAnimationFrame(() => jumpToEvidence(id));
  };
  const fold = (k: FoldKey) => ({ open: open(k), onToggle: () => toggle(k), summary: sectionSummary(k, report) });

  return (
    <article className="trend-report-print space-y-3" aria-label="트렌드 분석 보고서">
      {/* 인쇄(PDF) 때만 보이는 머리글 한 줄 */}
      <p className="hidden border-b border-ink-100 pb-1 text-xs text-ink-500 print:block">{printHeaderLine(report, company?.name)}</p>
      <header className="card space-y-2.5 p-4 sm:p-5 trend-report-section">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-bold text-ink-900">트렌드 분석 보고서{report.brief.product ? ` · ${report.brief.product}` : ""}</h2>
          <StatusChip label={st.label} t={st.tone} />
          <span className="chip bg-ink-100 text-xs font-semibold tabular-nums text-ink-700">{reportLabel(report.report_id)}</span>
          {!readOnly && <span className="ml-auto flex flex-wrap gap-2 print-hide">
            {onRebuild &&<button type="button" className="btn-quiet" onClick={() => onRebuild(report.brief)}>의뢰 고쳐서 다시 만들기</button>}
            <button type="button" className="btn-ghost" onClick={() => void printReport()}>PDF로 저장</button>
            {finished && planPath && <button type="button" className="btn-primary" onClick={() => navigate(planPath)}>기획서 보기</button>}
            {finished && !planPath && (delivery.sent || sent) && <DeliveryBadge chip={delivery} />}
            {finished && !planPath && <HandoffButton reportId={report.report_id} already={delivery.sent || !!sent} onSent={setSent} className="btn-ghost" />}
          </span>}
        </div>
        <p className="text-xs text-ink-500">
          {when(report.created_at)} 의뢰 · {CALLER_LABEL[report.caller] ?? report.caller}{report.finished_at && ` · ${when(report.finished_at)} 끝남`}
        </p>
        {keepPolling(report.status) && <Progress report={report} />}
        {!keepPolling(report.status) && <SectionNotes report={report} />}
        <BriefSummary brief={report.brief} theme={sec.theme} />
      </header>

      <Conclusion s={sec.recommendation} summaryReason={report.summary_reason} onJump={jump} />

      <div className="flex flex-wrap items-center gap-2 pt-2">
        <h3 className="text-base font-bold text-ink-900">근거 데이터</h3>
        <EvidenceChip kind="api" /><EvidenceChip kind="ai" /><EvidenceChip kind="human" />
        <span className="text-xs text-ink-500 print-hide">머리를 누르면 펼쳐요</span>
      </div>
      {sec.theme && showThemeSection(report) && (
        <Fold no={0} title={`주제 후보 · ${sec.theme.theme ?? ""}${sec.theme.target_word ? ` (${sec.theme.target_word})` : ""}`}
          part="theme" status={sec.theme.status} reason={sec.theme.reason} {...fold("theme")}>
          <ThemeBody s={sec.theme} />
        </Fold>
      )}
      <Fold no={1} title="검색" part="search" status={sec.search.status} reason={sec.search.reason} {...fold("search")}>
        <SearchBody s={sec.search} />
      </Fold>
      <Fold no={2} title="해시태그" part="hashtags" status={sec.hashtags.status} reason={sec.hashtags.reason} {...fold("hashtags")}>
        <HashtagBody s={sec.hashtags} brief={report.brief} />
      </Fold>
      <Fold no={3} title="콘텐츠" part="content" status={sec.content.status} reason={sec.content.reason} {...fold("content")}>
        <ContentBody s={sec.content} />
      </Fold>
      <Fold no={4} title="경쟁사" part="competitors" status={sec.competitors.status} reason={sec.competitors.reason} {...fold("competitors")}>
        <CompetitorBody s={sec.competitors} brief={report.brief} />
      </Fold>
      {/* 다음 단계: 이 보고서에서 뽑은 기획 인사이트 (인사이트 탭과 같은 카드). 영상 안쪽 분석은 아직 준비 중이라 한 줄로만 */}
      {finished && !readOnly && (
        <section className="space-y-1.5 print-hide" aria-label="다음 단계 · 기획 인사이트">
          <ul><ReportInsightCard reportId={report.report_id} createdAt={report.created_at} sent={sent} onSent={setSent} /></ul>
          <p className="flex flex-wrap items-center gap-2 text-xs text-ink-500">영상 안쪽 분석 · 유료 광고 — 담당자 업로드 <PreparingChip /></p>
        </section>
      )}

      <Fold title="근거와 한계" {...fold("limits")}>
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
      </Fold>
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
    // 그린 다음 프레임에 인쇄 (보고서가 화면에 들어간 뒤, 이미지를 기다린 다음)
    const t = setTimeout(() => { onPrinted?.(); void printReport(); }, 0);
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
