import { useCallback, useEffect, useState } from "react";
import { Bars, Loading, OFFLINE, Stat } from "@/components/admin/bits";
import { Stars } from "@/components/Stars";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import { CATEGORY_KO } from "@/lib/labels";
import { Link } from "@/lib/nav";
import type { FeedbackItem, FeedbackOverview, FeedbackStatus } from "@/lib/types";

// 후기 관리 = 해결된 문의에 고객이 남긴 평가(별점·해결 여부·한 줄 후기)를 모아 만족도를 보고 후기를 검토하는 화면.
// 숫자는 고객지원이 코드로 센 값이고 화면이 새로 계산하지 않는다(평가가 없으면 0이 아니라 "아직 없음").
// 개인정보·링크/연락 유도·비속어·광고성·반복 문자가 있는 후기는 자동으로 확인 대기가 되고, 상담원이 공개하거나 숨긴다.
// 낮은 별점(2점 이하)이나 "해결 안 됨" 답은 공개와 별개로 선제 연락 대상이다. 숨긴 후기도 만족도에는 센다.

const FLAG_KO: Record<string, string> = {
  PERSONAL_INFO: "개인정보", LINK_OR_CONTACT: "링크·연락 유도", PROFANITY: "비속어", PROMOTION: "광고성", REPEATED_CHARS: "반복 문자", CS_FOLLOW_UP: "선제 연락 대상",
};
const STATUS: Record<FeedbackStatus, { label: string; tone: string }> = {
  flagged: { label: "확인 대기", tone: "bg-amber-50 text-amber-700" },
  visible: { label: "공개", tone: "bg-emerald-50 text-emerald-700" },
  hidden: { label: "숨김", tone: "bg-ink-100 text-ink-500" },
};
type Filter = "all" | "flagged" | "followup" | "hidden";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "전체" }, { id: "flagged", label: "확인 대기" }, { id: "followup", label: "선제 연락 대상" }, { id: "hidden", label: "숨김" },
];
const REFRESH_MS = 30_000;

const pct = (v: number | null) => (v == null ? "아직 없음" : `${Math.round(v * 100)}%`);
const isFollowUp = (r: FeedbackItem) => r.feedback.flags.some((f) => f.code === "CS_FOLLOW_UP");

export default function ReviewsPage() {
  const [rows, setRows] = useState<FeedbackItem[] | null>(null);
  const [stats, setStats] = useState<FeedbackOverview | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.admin.feedback(), api.admin.feedbackStats()]);
      setRows(r); setStats(s); setError(null);
    } catch (e) { setError((e as Error).message || OFFLINE); }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  async function decide(inquiryId: number, status: "visible" | "hidden") {
    setBusy(inquiryId); setError(null);
    try { await api.admin.setFeedbackStatus(inquiryId, status); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  if (!rows) return <Loading error={error} />;
  const shown = rows.filter((r) => filter === "all" || (filter === "followup" ? isFollowUp(r) : r.feedback.status === filter));
  const count = (f: Filter) => rows.filter((r) => f === "all" || (f === "followup" ? isFollowUp(r) : r.feedback.status === f)).length;
  const dist = stats ? Object.fromEntries([5, 4, 3, 2, 1].map((n) => [String(n), stats.distribution[String(n)] ?? 0])) : {};

  return (
    <div className="space-y-5 px-8 pb-10 pt-2">
      <div>
        <h1 className="text-xl font-semibold">후기 관리</h1>
        <p className="mt-1 text-sm text-ink-500">
          문제가 해결된 문의에 고객이 남긴 평가(별점·해결 여부·한 줄 후기)를 모아 만족도를 봅니다. 개인정보·링크·비속어·광고성 문구가 있는 후기는 자동으로 확인 대기가 되고,
          낮은 별점이나 &quot;해결 안 됨&quot; 답은 공개해도 선제 연락 대상으로 표시합니다. 후기는 상담 콘솔에서 해결로 닫은 문의에 남길 수 있습니다.
        </p>
      </div>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}

      {stats && (
        <>
          <div className="flex flex-wrap gap-3">
            <Stat label="평균 별점" value={stats.average_rating == null ? "아직 없음" : <span className="inline-flex items-center gap-2">{stats.average_rating.toFixed(1)}<Stars value={stats.average_rating} size={13} /></span>} sub={`평가 ${stats.rated}건`} />
            <Stat label="만족 비율 (4~5점)" value={pct(stats.satisfied_rate)} sub="평가 중 4점 이상" />
            <Stat label="해결됐다고 답한 비율" value={pct(stats.solved_rate)} sub="고객이 직접 답한 값" />
            <Stat label="평가 응답률" value={pct(stats.response_rate)} sub={`해결된 문의 ${stats.resolved_inquiries}건 중 ${stats.rated}건`} />
            <Stat label="확인 대기" value={stats.pending_review} tone={stats.pending_review ? "text-amber-700" : ""} sub={`숨김 ${stats.hidden}건`} />
            <Stat label="선제 연락 대상" value={stats.follow_up} tone={stats.follow_up ? "text-red-700" : ""} sub="낮은 별점 또는 해결 안 됨" />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <section className="card p-4" aria-label="별점 분포">
              <h2 className="mb-2 text-[13px] font-semibold">별점 분포</h2>
              <Bars data={dist} labels={{ "5": "5점", "4": "4점", "3": "3점", "2": "2점", "1": "1점" }} color="bg-amber-500" />
            </section>
            <section className="card p-4" aria-label="분류별 만족도">
              <h2 className="mb-2 text-[13px] font-semibold">분류별 평균 별점</h2>
              {Object.keys(stats.by_category).length === 0 ? <p className="text-xs text-ink-500">아직 평가가 없습니다.</p> : (
                <ul className="space-y-1.5 text-xs">
                  {Object.entries(stats.by_category).map(([cat, v]) => (
                    <li key={cat} className="flex items-center gap-2">
                      <span className="w-24 shrink-0">{CATEGORY_KO[cat] ?? cat}</span>
                      <Stars value={v.average} size={12} />
                      <span className="tabular-nums text-ink-700">{v.average == null ? "–" : v.average.toFixed(1)}</span>
                      <span className="ml-auto tabular-nums text-ink-500">{v.count}건</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="후기 거르기">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
            className={`chip py-1 ${filter === f.id ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{f.label} {count(f.id)}</button>
        ))}
      </div>

      <ul className="space-y-3">
        {shown.map((r) => {
          const fb = r.feedback;
          const st = STATUS[fb.status] ?? STATUS.visible;
          return (
            <li key={r.inquiry_id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className={`chip ${st.tone}`}>{st.label}</span>
                <Stars value={fb.rating} size={13} /><b>{r.customer_name}</b>
                <span className="text-ink-500">· {r.category ? CATEGORY_KO[r.category] ?? r.category : "분류 없음"} · {dateStr(fb.created_at)}</span>
                <span className={`chip ${fb.resolved ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>{fb.resolved ? "해결됐다고 답함" : "해결 안 됐다고 답함"}</span>
                {fb.source === "console" && <span className="chip bg-ink-100 text-ink-500">시험 평가</span>}
                <div className="ml-auto flex items-center gap-1.5">
                  <Link href={`/console?id=${r.inquiry_id}`} className="text-brand-700 underline">문의 #{r.inquiry_id} 보기</Link>
                  {fb.status !== "visible" && <button className="btn-primary py-1 text-xs" disabled={busy === r.inquiry_id} onClick={() => decide(r.inquiry_id, "visible")}>공개</button>}
                  {fb.status !== "hidden" && <button className="btn-ghost py-1 text-xs" disabled={busy === r.inquiry_id} onClick={() => decide(r.inquiry_id, "hidden")}>숨김</button>}
                </div>
              </div>
              <p className="mt-2 text-[11px] text-ink-500">{r.subject}</p>
              {fb.comment ? <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-700">{fb.comment}</p> : <p className="mt-1 text-sm text-ink-500">남긴 글이 없습니다.</p>}
              {fb.flags.length > 0 && (
                <ul className="mt-2 space-y-1">{fb.flags.map((f) => <li key={f.code} className={`rounded-md px-2 py-1 text-xs ${f.info ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}><b className="mr-1">{FLAG_KO[f.code] ?? f.code}</b>{f.message}</li>)}</ul>
              )}
              {fb.decided_by && <p className="mt-2 text-[11px] text-ink-500">{fb.status === "visible" ? "공개" : "숨김"}로 정함 · {fb.decided_by}{fb.decided_at ? ` · ${dateStr(fb.decided_at)}` : ""}</p>}
            </li>
          );
        })}
        {!shown.length && <li className="py-10 text-center text-sm text-ink-500">{rows.length ? "이 조건의 후기가 없습니다." : "아직 받은 후기가 없습니다. 문의를 해결로 닫으면 고객이 평가를 남길 수 있습니다."}</li>}
      </ul>
      <p className="text-[11px] text-ink-500">후기 삭제를 조건으로 환불이나 보상을 제안하지 않습니다. 답변 초안 가드가 이런 문장을 차단합니다.</p>
    </div>
  );
}
