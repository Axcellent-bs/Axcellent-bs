import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { TONE, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { when } from "@/components/data/Sources";
import { TrendReportView } from "@/components/data/TrendReportView";
import { dataAi, type TrendReportInput, type TrendReportListItem } from "@/lib/data";
import { sectionState, type SectionState } from "@/lib/segmentView";
import { TREND_REPORT_PRESETS } from "@/lib/trendReportPresets";
import {
  AGE_OPTIONS, CALLER_LABEL, EMPTY_FORM, GENDER_LABEL, REPORT_STATUS, buildInput, formFromBrief, hasInput, reportHash, reportIdFromHash, type RequestForm,
} from "@/lib/trendReportView";

// 트렌드&콘텐츠 "분석 의뢰" 하위 탭 (#request, 루프 단계와 별개).
// - 위: 의뢰문 붙여 넣기(여러 줄) → 넣고 만들면 request_text만 보낸다
// - 접힘 "직접 정하기": 프리셋(친환경 화장품 = eco_beauty, 값은 lib/trendReportPresets.ts) → 제품·타깃 성별·연령·결과물·경쟁사·키워드
// - "보고서 만들기" → trend_report_create → 주소 #request?id=<report_id>로 보고서를 연다(TrendReportView).
//   같은 의뢰 저장본을 열었으면(reused) "다시 만들기"(rebuild=true)
// - 오른쪽(좁으면 아래): 지난 의뢰 목록(trend_report_list). 누르면 보고서를 연다
// - skill이 아직 없으면(403·FORBIDDEN_SKILL) 그 칸만 "준비 중" (lib/segmentView sectionState)

function Preparing({ what }: { what: string }) {
  return (
    <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
      <PreparingChip /> {what} 기능을 데이터 AI에 붙이는 중이에요. 준비되면 여기에 나옵니다.
    </p>
  );
}

function Problem({ state, what, onRetry }: { state: SectionState; what: string; onRetry?: () => void }) {
  if (state.kind === "missing") return <Preparing what={what} />;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">
      <span className="min-w-0 flex-1">{what}: {state.message}</span>
      {onRetry && <button type="button" className="btn-ghost" onClick={onRetry}>다시 시도</button>}
    </div>
  );
}

type ListLoad = { kind: "loading" } | { kind: "ok"; items: TrendReportListItem[] } | { kind: "error"; state: SectionState };

function useReportList(run: number): { list: ListLoad; reload: () => void } {
  const [list, setList] = useState<ListLoad>({ kind: "loading" });
  const [n, setN] = useState(0);
  const reload = useCallback(() => setN((x) => x + 1), []);
  useEffect(() => {
    let live = true;
    dataAi.trendReportList(20)
      .then((r) => { if (live) setList({ kind: "ok", items: r.output.items }); })
      .catch((e: Error) => { if (live) setList({ kind: "error", state: sectionState(e.message) }); });
    return () => { live = false; };
  }, [run, n]);
  return { list, reload };
}

function PastList({ list, current, onOpen, onRetry }: { list: ListLoad; current: string | null; onOpen: (id: string) => void; onRetry: () => void }) {
  return (
    <section className="card min-w-0 space-y-2 p-4 print-hide" aria-labelledby="trend-request-past">
      <h3 id="trend-request-past" className="text-base font-semibold text-ink-900">지난 의뢰</h3>
      {list.kind === "loading" ? <Waiting />
        : list.kind === "error" ? <Problem state={list.state} what="지난 의뢰" onRetry={onRetry} />
        : list.items.length === 0 ? <p className="text-sm text-ink-500">아직 의뢰가 없어요.</p>
        : (
          <ul className="space-y-1.5">
            {list.items.map((it) => {
              const st = REPORT_STATUS[it.status];
              return (
                <li key={it.report_id}>
                  <button type="button" onClick={() => onOpen(it.report_id)} aria-current={current === it.report_id ? "true" : undefined}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition hover:border-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${current === it.report_id ? "border-brand-500 bg-brand-50" : "border-ink-100"}`}>
                    <span className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
                      <span className={`chip text-[11px] ${TONE[st.tone]}`}>{st.label}</span>
                      {when(it.created_at)} · {CALLER_LABEL[it.caller] ?? it.caller}
                    </span>
                    <span className="mt-0.5 block truncate text-sm font-medium text-ink-900">{it.product ?? "제품 정하지 않음"}</span>
                    {it.summary_line && <span className="block truncate text-xs text-ink-700">{it.summary_line}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
    </section>
  );
}

function RequestFormCard({ form, setForm, busy, onSubmit, error, editing, onCancel }: {
  form: RequestForm; setForm: (f: RequestForm) => void; busy: boolean; onSubmit: () => void; error: SectionState | null;
  /** 보고서를 고쳐서 다시 만드는 중: 직접 정하기를 펴 두고 rebuild로 보낸다 */
  editing?: boolean; onCancel?: () => void;
}) {
  const set = <K extends keyof RequestForm>(k: K, v: RequestForm[K]) => setForm({ ...form, [k]: v });
  const pickPreset = (id: string) => {
    const p = TREND_REPORT_PRESETS.find((x) => x.id === id);
    if (!p) return setForm({ ...form, preset: "" });
    setForm({
      ...form, preset: p.id, product: p.product, gender: p.target_gender ?? "all", ages: [...p.target_ages], format: p.format,
      competitors: p.competitors.join(", "), keywords: p.keywords.join(", "),
    });
  };
  const toggleAge = (a: string) => set("ages", form.ages.includes(a) ? form.ages.filter((x) => x !== a) : [...form.ages, a]);
  const submit = (e: FormEvent) => { e.preventDefault(); if (!busy && hasInput(form)) onSubmit(); };
  const pasted = form.requestText.trim().length > 0;
  return (
    <form onSubmit={submit} className="card min-w-0 space-y-3 p-4 sm:p-5 print-hide" aria-labelledby="trend-request-title">
      <h3 id="trend-request-title" className="text-base font-semibold text-ink-900">{editing ? "의뢰 고쳐서 다시 만들기" : "트렌드 분석 의뢰"}</h3>
      <label className="flex flex-col gap-1 text-sm text-ink-700">
        의뢰문 붙여 넣기
        <textarea className="field min-h-[120px] w-full" value={form.requestText} onChange={(e) => set("requestText", e.target.value)}
          placeholder="총괄·기획이 보낸 의뢰 문장을 그대로 붙여 넣으세요. 제품·타깃·결과물·경쟁사·키워드를 데이터 AI가 읽어 정합니다." />
      </label>
      <details key={editing ? "edit" : "new"} open={editing || undefined} className="rounded-lg border border-ink-100 px-3 py-2">
        <summary className="cursor-pointer select-none text-sm font-medium text-ink-700">직접 정하기{pasted && <span className="ml-2 text-xs font-normal text-ink-500">의뢰문이 있으면 의뢰문만 보냅니다</span>}</summary>
        <fieldset disabled={pasted} className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs text-ink-500">
            프리셋
            <select className="field" value={form.preset} onChange={(e) => pickPreset(e.target.value)}>
              <option value="">고르지 않음</option>
              {TREND_REPORT_PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-500">
            제품
            <input className="field" value={form.product} onChange={(e) => set("product", e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-500">
            목표
            <input className="field" value={form.goal} onChange={(e) => set("goal", e.target.value)} placeholder="브랜드 인지도" />
          </label>
          <div className="flex flex-col gap-1 text-xs text-ink-500" role="group" aria-label="타깃 성별">
            타깃 성별
            <span className="flex flex-wrap gap-3 text-sm text-ink-700">
              {(["f", "m", "all"] as const).map((g) => (
                <label key={g} className="inline-flex items-center gap-1">
                  <input type="radio" name="trend-request-gender" checked={form.gender === g} onChange={() => set("gender", g)} />
                  {g === "all" ? "전체" : GENDER_LABEL[g]}
                </label>
              ))}
            </span>
          </div>
          <div className="flex flex-col gap-1 text-xs text-ink-500" role="group" aria-label="타깃 연령">
            타깃 연령
            <span className="flex flex-wrap gap-3 text-sm text-ink-700">
              {AGE_OPTIONS.map((a) => (
                <label key={a} className="inline-flex items-center gap-1">
                  <input type="checkbox" checked={form.ages.includes(a)} onChange={() => toggleAge(a)} />{a}대
                </label>
              ))}
            </span>
          </div>
          <label className="flex flex-col gap-1 text-xs text-ink-500">
            결과물
            <input className="field" value={form.format} onChange={(e) => set("format", e.target.value)} placeholder="인스타 릴스 30초" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-500">
            경쟁사 (인스타 아이디, 쉼표로)
            <input className="field" value={form.competitors} onChange={(e) => set("competitors", e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-500 sm:col-span-2">
            키워드 (쉼표로)
            <input className="field" value={form.keywords} onChange={(e) => set("keywords", e.target.value)} />
          </label>
        </fieldset>
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className="btn-primary" disabled={busy || !hasInput(form)}>{busy ? "의뢰 넣는 중…" : editing ? "고쳐서 다시 만들기" : "보고서 만들기"}</button>
        {editing && onCancel && <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>취소</button>}
        {!hasInput(form) && <span className="text-xs text-ink-500">의뢰문을 붙여 넣거나 직접 정할 칸을 채워 주세요.</span>}
      </div>
      {error && <Problem state={error} what="분석 의뢰" />}
    </form>
  );
}

export function TrendRequest() {
  const location = useLocation();
  const navigate = useNavigate();
  const id = reportIdFromHash(location.hash);
  const open = useCallback((rid: string | null) => {
    navigate({ pathname: location.pathname, search: location.search, hash: rid ? reportHash(rid) : "#request" });
  }, [navigate, location.pathname, location.search]);

  const [form, setForm] = useState<RequestForm>(EMPTY_FORM);
  // 열어 둔 보고서의 의뢰를 고쳐서 다시 만드는 중 (폼을 직접 정하기로 미리 채워 연다)
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SectionState | null>(null);
  // 마지막으로 넣은 의뢰: 저장본을 열었으면(reused) 같은 입력으로 다시 만들기
  const [last, setLast] = useState<{ id: string; reused: boolean; input: TrendReportInput } | null>(null);
  const [listRun, setListRun] = useState(0);
  const { list, reload } = useReportList(listRun);

  const create = useCallback((input: TrendReportInput) => {
    setBusy(true);
    setError(null);
    dataAi.trendReportCreate(input)
      .then((r) => {
        setLast({ id: r.output.report_id, reused: r.output.reused, input });
        setListRun((x) => x + 1);
        setEditing(false);
        open(r.output.report_id);
      })
      .catch((e: Error) => setError(sectionState(e.message)))
      .finally(() => setBusy(false));
  }, [open]);

  const reusedHere = last && last.id === id && last.reused;
  return (
    // 보고서를 열면 보고서가 넓게 쓰도록 지난 의뢰 목록은 아래로
    <div className={`grid grid-cols-1 gap-4 ${id ? "" : "lg:grid-cols-[minmax(0,1fr)_320px]"}`}>
      <div className="min-w-0 space-y-4">
        {id && !editing ? (
          <>
            <div className="flex flex-wrap items-center gap-2 print-hide">
              <button type="button" className="btn-ghost" onClick={() => open(null)}>← 새 의뢰</button>
              {reusedHere && (
                <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  같은 의뢰 저장본을 열었어요
                  <button type="button" className="btn-ghost" disabled={busy} onClick={() => create({ ...last.input, rebuild: true })}>
                    {busy ? "다시 만드는 중…" : "다시 만들기"}
                  </button>
                </p>
              )}
              {error && <Problem state={error} what="분석 의뢰" />}
            </div>
            <TrendReportView key={id} id={id} onRebuild={(b) => { setForm(formFromBrief(b)); setError(null); setEditing(true); }} />
          </>
        ) : (
          <RequestFormCard form={form} setForm={setForm} busy={busy} error={error} editing={editing} onCancel={() => setEditing(false)}
            onSubmit={() => create(buildInput(form, editing))} />
        )}
      </div>
      <PastList list={list} current={id} onOpen={open} onRetry={reload} />
    </div>
  );
}
