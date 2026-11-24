import { useCallback, useEffect, useState } from "react";
import { Skeleton } from "@/components/finance/bits";
import { humanTokenSet, todayKst } from "@/lib/finance";
import { briefs, type StoredBrief } from "@/lib/briefs";
import {
  countText, digitsOnly, formProblem, krwText, mediumKo, mediumsFor, missingNotes, recordOutcomeKo, sourceKo, toBody, withMedium, withSource,
  type AdMetricsForm, type AdMetricsRow, type AdMetricsView,
} from "@/lib/adMetrics";
import { adMetricsApi, adMetricsEnabled } from "@/lib/adMetricsApi";
import { TrackedLinkBox } from "./TrackedLinkBox";

// 받은 기획안 캠페인별 광고 숫자. 광고 플랫폼 연결이 없어 사람이 보고서를 보고 날짜별로 적는다.
// - 빈칸은 모름, 0은 0원. 저장된 줄에서도 모름은 "–", 0은 "0원"으로 다르게 보인다
// - 클릭률 같은 비율은 만들지 않는다(데이터분석이 계산한다). 줄과 칸별 합계만 보인다
// - source → medium 조합표는 서버 응답(combos)에서 받는다
// - 사람 토큰이 없으면 입력을 잠그고 읽기만 한다(CampaignControl과 같은 규칙)

const blankForm = (date: string): AdMetricsForm => ({ date, source: "", medium: "", impressions: "", clicks: "", spend: "", reportRef: "" });
const fromRow = (r: AdMetricsRow): AdMetricsForm => ({
  date: r.date, source: r.source, medium: r.medium,
  impressions: r.impressions === null ? "" : String(r.impressions),
  clicks: r.clicks === null ? "" : String(r.clicks),
  spend: r.spend_krw === null ? "" : String(r.spend_krw),
  reportRef: r.report_ref ?? "",
});

export function AdMetricsPanel() {
  const [items, setItems] = useState<StoredBrief[] | null>(null);
  const [listNote, setListNote] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [campaignId, setCampaignId] = useState("");

  useEffect(() => {
    if (!adMetricsEnabled) return;
    let alive = true;
    briefs.list()
      .then((list) => {
        if (!alive) return;
        setItems(list.items);
        setListNote(list.items.length === 0 ? list.unknown_reason ?? "받은 기획안이 없습니다." : null);
        setCampaignId((cur) => cur || list.items[0]?.campaign_id || "");
      })
      .catch((e: Error) => { if (alive) setListError(`받은 기획안을 읽지 못했습니다: ${e.message}`); });
    return () => { alive = false; };
  }, []);

  return (
    <section className="card space-y-4 p-4 sm:p-5">
      <div>
        <h2 className="text-base font-semibold text-ink-900">광고 숫자</h2>
        <p className="mt-0.5 text-sm text-ink-500">받은 기획안의 캠페인마다 광고 보고서를 보고 날짜별 노출·클릭·광고비를 적습니다. 광고 플랫폼 연결이 없어 사람이 적은 값을 그대로 둡니다. 클릭률 같은 비율은 데이터 AI가 계산하므로 여기서는 줄과 합계만 보입니다.</p>
      </div>
      {!adMetricsEnabled && <p className="py-4 text-center text-sm text-ink-500">화면 중계 서버(dashboard-api) 주소가 설정되지 않아 광고 숫자를 읽을 수 없습니다.</p>}
      {listError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{listError}</p>}
      {adMetricsEnabled && !items && !listError && <Skeleton stats={0} lines={2} />}
      {listNote && <p className="py-4 text-center text-sm text-ink-500">{listNote} 기획안을 받으면 그 캠페인의 광고 숫자를 적을 수 있습니다.</p>}
      {items && items.length > 0 && (
        <>
          <label className="block text-sm"><span className="text-ink-700">캠페인</span>
            <select className="field mt-0.5 w-full sm:w-auto" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
              {items.map((i) => <option key={i.campaign_id} value={i.campaign_id}>{i.brief.title} ({i.campaign_id})</option>)}
            </select>
          </label>
          {campaignId && <CampaignAdMetrics key={campaignId} campaignId={campaignId} />}
        </>
      )}
    </section>
  );
}

function CampaignAdMetrics({ campaignId }: { campaignId: string }) {
  const [view, setView] = useState<AdMetricsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<AdMetricsForm>(() => blankForm(todayKst()));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await adMetricsApi.get(campaignId));
      setError(null);
    } catch (e) { setError(`광고 숫자를 읽지 못했습니다: ${(e as Error).message}`); }
  }, [campaignId]);
  useEffect(() => { void load(); }, [load]);

  const combos = view?.combos ?? {};
  const today = todayKst();
  const problem = view ? formProblem(form, combos, today) : null;
  const locked = !humanTokenSet;

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const answer = await adMetricsApi.record(campaignId, toBody(form));
      setMessage({ text: recordOutcomeKo(answer), ok: answer.outcome !== "rejected" });
      if (answer.outcome !== "rejected") setForm(blankForm(form.date));
      await load();
    } catch (e) { setMessage({ text: `저장하지 못했습니다: ${(e as Error).message}`, ok: false }); }
    finally { setBusy(false); }
  }

  const set = (key: "impressions" | "clicks" | "spend") => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: digitsOnly(e.target.value) }));
  const mediums = mediumsFor(combos, form.source);

  if (error) return <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>;
  if (!view) return <Skeleton stats={0} lines={2} />;

  return (
    <div className="space-y-4">
      <fieldset disabled={locked || busy} className="rounded-2xl border border-ink-100 p-4 text-sm">
        <legend className="px-1 font-medium text-ink-900">한 줄 적기</legend>
        {locked && <p className="mb-2 text-xs text-amber-800">사람 토큰이 없어 읽기만 됩니다. 광고 숫자는 마케팅 담당 사람 토큰으로만 적습니다.</p>}
        <div className="grid gap-2 sm:grid-cols-[150px_150px_150px_1fr_1fr_1fr]">
          <label className="block"><span className="text-ink-700">날짜(KST)</span>
            <input type="date" className="field mt-0.5 w-full" max={today} value={form.date} onChange={(e) => setForm((f) => ({ ...f, date: e.target.value }))} />
          </label>
          <label className="block"><span className="text-ink-700">source</span>
            <select className="field mt-0.5 w-full" value={form.source} onChange={(e) => setForm((f) => withSource(f, combos, e.target.value))}>
              <option value="">고르세요</option>
              {Object.keys(combos).map((s) => <option key={s} value={s}>{sourceKo(s)} ({s})</option>)}
            </select>
          </label>
          <label className="block"><span className="text-ink-700">medium</span>
            <select className="field mt-0.5 w-full" value={form.medium} disabled={mediums.length === 0} onChange={(e) => setForm((f) => withMedium(f, e.target.value))}>
              <option value="">{form.source ? "고르세요" : "source 먼저"}</option>
              {mediums.map((m) => <option key={m} value={m}>{mediumKo(m)} ({m})</option>)}
            </select>
          </label>
          <label className="block"><span className="text-ink-700">노출(회)</span><input className="field mt-0.5 w-full" inputMode="numeric" placeholder="모름" value={form.impressions} onChange={set("impressions")} /></label>
          <label className="block"><span className="text-ink-700">클릭(회)</span><input className="field mt-0.5 w-full" inputMode="numeric" placeholder="모름" value={form.clicks} onChange={set("clicks")} /></label>
          <label className="block"><span className="text-ink-700">광고비(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" placeholder="모름" value={form.spend} onChange={set("spend")} /></label>
        </div>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="block min-w-0 flex-1"><span className="text-ink-700">보고서 근거(선택)</span>
            <input className="field mt-0.5 w-full" maxLength={120} placeholder="예: 구글 광고 보고서 11/20 화면" value={form.reportRef} onChange={(e) => setForm((f) => ({ ...f, reportRef: e.target.value }))} />
          </label>
          <button className="btn-primary" disabled={locked || busy || problem !== null} onClick={() => void save()}>{busy ? "저장 중" : "저장"}</button>
        </div>
        <p className="mt-1 text-xs text-ink-500">비우면 모름, 0을 쓰면 0원(0회)으로 저장합니다. 사이트 안(onsite) 줄은 광고비를 0으로 채워 두지만 바꿀 수 있습니다. 같은 날짜·source·medium을 다시 적으면 고쳐 씁니다.</p>
        {problem && form.source && <p className="mt-1 text-xs text-red-800">{problem}</p>}
      </fieldset>
      {message && <p role={message.ok ? "status" : "alert"} className={`rounded-xl px-4 py-2 text-sm ${message.ok ? "bg-brand-50 text-brand-700" : "bg-red-50 text-red-800"}`}>{message.text}</p>}

      <SavedRows view={view} locked={locked} onEdit={(r) => { setForm(fromRow(r)); setMessage(null); }} />
      <TrackedLinkBox campaignId={campaignId} combos={combos} defaultBaseUrl={view.default_base_url} />
    </div>
  );
}

function SavedRows({ view, locked, onEdit }: { view: AdMetricsView; locked: boolean; onEdit: (r: AdMetricsRow) => void }) {
  if (view.status === "unknown" || view.rows.length === 0) {
    return <p className="rounded-2xl border border-ink-100 py-6 text-center text-sm text-ink-500">{view.reason ?? "아직 입력 없음"}<span className="mt-1 block text-xs">광고 보고서를 보고 위 칸에 적으면 여기에 쌓입니다.</span></p>;
  }
  const rows = [...view.rows].sort((a, b) => b.date.localeCompare(a.date) || a.source.localeCompare(b.source));
  const totals = view.totals;
  const notes = missingNotes(totals);
  return (
    <div className="overflow-x-auto rounded-2xl border border-ink-100">
      <table className="w-full text-sm">
        <thead className="bg-ink-50 text-left text-xs text-ink-500">
          <tr>
            <th className="px-3 py-2 font-medium">날짜</th><th className="px-3 py-2 font-medium">source · medium</th>
            <th className="px-3 py-2 text-right font-medium">노출</th><th className="px-3 py-2 text-right font-medium">클릭</th>
            <th className="px-3 py-2 text-right font-medium">광고비</th><th className="px-3 py-2 font-medium">근거 · 적은 사람</th><th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {rows.map((r) => (
            <tr key={`${r.date}|${r.source}|${r.medium}`}>
              <td className="px-3 py-2 tabular-nums">{r.date}</td>
              <td className="px-3 py-2">{sourceKo(r.source)} · {mediumKo(r.medium)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{countText(r.impressions)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{countText(r.clicks)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{krwText(r.spend_krw)}</td>
              <td className="px-3 py-2 text-xs text-ink-500">{r.report_ref ?? "근거 없음"} · {r.entered_by}{r.revisions > 0 ? ` · ${r.revisions}번 고침` : ""}</td>
              <td className="px-3 py-2 text-right"><button className="btn-ghost" disabled={locked} onClick={() => onEdit(r)}>고치기</button></td>
            </tr>
          ))}
        </tbody>
        {totals && (
          <tfoot className="border-t border-ink-100 bg-ink-50 font-medium">
            <tr>
              <td className="px-3 py-2" colSpan={2}>합계 ({rows.length}줄)</td>
              <td className="px-3 py-2 text-right tabular-nums">{countText(totals.impressions)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{countText(totals.clicks)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{krwText(totals.spend_krw)}</td>
              <td className="px-3 py-2" colSpan={2} />
            </tr>
          </tfoot>
        )}
      </table>
      {notes.length > 0 && <p className="border-t border-ink-100 px-3 py-2 text-xs text-ink-500">모르는 줄이 섞인 칸은 합계를 내지 않습니다(–). {notes.join(" · ")}</p>}
    </div>
  );
}
