import { useEffect, useState } from "react";
import { finance, humanTokenSet, signedKrw, type OtherPnlInput, type StatementPeriod } from "@/lib/finance";
import { AppliedRules, Panel, ResourceBody, useResource } from "./bits";

// 재무제표: 일반 회사가 쓰는 손익계산서 순서(K-IFRS 기능별)로 당기·전기를 나란히 보여 주고 엑셀·CSV로 내려받는다.
// 장부에 없는 항목(급여·임차료, 영업외손익, 법인세)은 "자료 없음"으로 둔다. 숫자를 지어내지 않는다.

const PERIODS: { id: StatementPeriod; label: string }[] = [
  { id: "month", label: "월" },
  { id: "quarter", label: "분기" },
  { id: "year", label: "연" },
];

const amount = (v: number | null) => (v == null ? "자료 없음" : signedKrw(v));
const tone = (v: number | null) => (v == null ? "text-ink-500" : v < 0 ? "text-red-700" : "");

export function StatementsSection({ tick }: { tick: number }) {
  const [period, setPeriod] = useState<StatementPeriod>("month");
  /** 비어 있으면 서버 기본값(오늘이 속한 기간) */
  const [date, setDate] = useState("");
  const [saved, setSaved] = useState(0);
  const res = useResource(`statements:${period}:${date}:${saved}`, () => finance.statements(period, date || null), tick);
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function download(format: "csv" | "xlsx") {
    setDownloading(format);
    setProblem(null);
    try {
      await finance.downloadStatement(period, date || null, format);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setDownloading(null);
    }
  }

  const tools = (
    <>
      <div role="group" aria-label="재무제표 기간" className="inline-flex rounded-full border border-ink-100 p-0.5 text-sm">
        {PERIODS.map((p) => (
          <button key={p.id} type="button" aria-pressed={period === p.id} onClick={() => setPeriod(p.id)}
            className={`rounded-full px-3 py-1 ${period === p.id ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {p.label}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-1 text-sm text-ink-500">
        <span className="sr-only">기준 날짜</span>
        <input type="date" className="field w-auto py-1" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <button type="button" className="btn-primary" disabled={downloading !== null} onClick={() => download("xlsx")}>
        {downloading === "xlsx" ? "내려받는 중…" : "엑셀 내려받기"}
      </button>
      <button type="button" className="btn-ghost" disabled={downloading !== null} onClick={() => download("csv")}>
        {downloading === "csv" ? "내려받는 중…" : "CSV 내려받기"}
      </button>
    </>
  );

  return (
    <Panel id="statements" title="재무제표" meta={res.env?.meta} tools={tools}
      hint="손익계산서를 일반 회사 양식(K-IFRS 기능별) 순서로 정리합니다. 중개 매출이라 매출은 판매 수수료이고, 회사가 낸 쿠폰은 매출에서 뺍니다. 장부에 없는 항목은 '자료 없음'으로 두니, 회계 장부와 맞춰 채워 쓰세요.">
      {problem && <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}
      <ResourceBody res={res}>
        {(s) => (
          <div className="space-y-4">
            <p className="text-sm text-ink-700">
              <b className="text-ink-900">{s.title}</b>{s.company ? ` · ${s.company}` : ""} · 당기 {s.current.name} · 전기 {s.previous.name} · 단위 {s.unit}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-left text-sm text-ink-500">
                  <tr>
                    <th className="pb-1 font-medium">과목</th>
                    <th className="pb-1 text-right font-medium">당기 ({s.current.name})</th>
                    <th className="pb-1 text-right font-medium">전기 ({s.previous.name})</th>
                    <th className="pb-1 pl-3 font-medium">비고</th>
                  </tr>
                </thead>
                <tbody>
                  {s.rows.map((r) => (
                    <tr key={r.code} className={`border-t border-ink-100 ${r.total ? "font-semibold text-ink-900" : ""}`}>
                      <th scope="row" className={`py-1.5 pr-3 text-left font-normal ${r.level ? "pl-4 text-ink-700" : ""}`}>{r.total ? <b>{r.label}</b> : r.label}</th>
                      <td className={`py-1.5 text-right tabular-nums ${tone(r.current)}`}>{amount(r.current)}</td>
                      <td className={`py-1.5 text-right tabular-nums ${tone(r.previous)}`}>{amount(r.previous)}</td>
                      <td className="py-1.5 pl-3 text-sm font-normal text-ink-500">{r.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-ink-900">주석(참고)</h3>
              <dl className="mt-2 grid gap-2 sm:grid-cols-3">
                {s.notes.map((n) => (
                  <div key={n.code} className="rounded-2xl bg-surface-2 px-3 py-2">
                    <dt className="text-sm text-ink-500">{n.label}</dt>
                    <dd className="text-sm font-semibold tabular-nums">
                      {n.current == null ? "자료 없음" : n.current.toLocaleString("ko-KR")}
                      <span className="ml-1 text-sm font-normal text-ink-500">전기 {n.previous == null ? "자료 없음" : n.previous.toLocaleString("ko-KR")}</span>
                    </dd>
                    {n.note && <dd className="text-sm text-ink-500">{n.note}</dd>}
                  </div>
                ))}
              </dl>
            </div>
            <p className="text-sm text-ink-500">작성 기준: {s.basis}. 재무상태표·현금흐름표는 자산·부채·현금 잔액 장부가 없어 만들지 않습니다.</p>
            {period === "month" && <OtherPnlForm month={s.current.from.slice(0, 7)} onSaved={() => setSaved((n) => n + 1)} />}
            <AppliedRules rules={s.applied_rules} />
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

const OTHER_FIELDS: { key: "salary" | "rent" | "other_sga" | "non_operating_income" | "non_operating_expense" | "income_tax"; label: string }[] = [
  { key: "salary", label: "급여" },
  { key: "rent", label: "임차료" },
  { key: "other_sga", label: "그 밖의 판관비" },
  { key: "non_operating_income", label: "영업외수익" },
  { key: "non_operating_expense", label: "영업외비용" },
  { key: "income_tax", label: "법인세 (세무사 확인값, 모르면 비움)" },
];

/** 쇼핑몰 장부에 없는 손익. 입력해야 재무제표 아래 칸이 계산된다. 지어내지 않으려고 자동으로 채우지 않는다 */
function OtherPnlForm({ month, onSaved }: { month: string; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [locked, setLocked] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    finance.otherPnl(month).then((r) => {
      if (!live) return;
      const entry = r.data.entry;
      setValues(Object.fromEntries(OTHER_FIELDS.map((f) => [f.key, entry?.[f.key] == null ? "" : String(entry[f.key])])));
      setLocked(r.data.locked);
      setInfo(entry ? `${entry.by} 입력` : null);
    }).catch(() => { if (live) setProblem("입력값을 읽지 못했습니다."); });
    return () => { live = false; };
  }, [month]);

  async function save() {
    setBusy(true);
    setProblem(null);
    const num = (k: string) => Number(values[k] || 0);
    const body: OtherPnlInput = {
      month, salary: num("salary"), rent: num("rent"), other_sga: num("other_sga"),
      non_operating_income: num("non_operating_income"), non_operating_expense: num("non_operating_expense"),
      income_tax: values.income_tax ? Number(values.income_tax) : null,
    };
    try {
      const r = await finance.saveOtherPnl(body);
      setInfo(r.data.entry ? `${r.data.entry.by} 입력` : null);
      onSaved();
    } catch (e) { setProblem((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <details className="rounded-2xl border border-ink-100 px-4 py-3 text-sm">
      <summary className="cursor-pointer font-semibold text-ink-900">{month} 장부 밖 손익 입력{info ? ` · ${info}` : ""}</summary>
      <p className="mt-1 text-ink-500">급여·임차료·영업외손익은 쇼핑몰 장부에 없습니다. 월 합계를 원 단위로 넣으면 아래 칸이 계산됩니다. 입력하지 않으면 "자료 없음"입니다.</p>
      {locked && <p className="mt-1 text-amber-800">확정한 달이라 고칠 수 없습니다. 차이는 결산 조정으로 처리합니다.</p>}
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        {OTHER_FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className="text-ink-700">{f.label}</span>
            <input className="field mt-0.5 w-full" inputMode="numeric" disabled={locked} value={values[f.key] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value.replace(/[^0-9]/g, "") }))} />
          </label>
        ))}
      </div>
      <button className="btn-primary mt-3" disabled={locked || busy || !humanTokenSet} onClick={save}>{busy ? "저장하는 중" : "저장"}</button>
      {problem && <p role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-red-800">{problem}</p>}
    </details>
  );
}
