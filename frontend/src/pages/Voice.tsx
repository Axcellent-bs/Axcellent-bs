import { useCallback, useEffect, useState } from "react";
import { Loading, OFFLINE, Stat } from "@/components/admin/bits";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import { humanTokenSet } from "@/lib/finance";
import { support, type IdeaDigestResult, type IdeaGroup, type IdeaItem, type IdeaList, type IdeaPeriod, type IdeaTrends } from "@/lib/support";

// 고객의 소리 = 고객이 "이런 걸 해 주면 좋겠다"고 보낸 아이디어를 모으는 곳. 불만·문의와 따로 쌓이고
// (환불·취소·불량 같은 문제는 아이디어로 받지 않고 문의로 안내한다), 개인정보는 가려서 저장된다.
// 같은 종류·영역의 아이디어를 묶어 건수·고객 수·많이 나온 말을 보여 주고, 담당자가 골라 기획에 넘긴다.
// 기획이 이벤트 요청이나 향후 방안 기획서로 이어 가고, 개발로 넘기는 일은 기획의 기존 흐름이다.
// 기획에는 고객 원문을 보내지 않고 건수·고객 수·기간·많이 나온 말만 보낸다. 원문은 이 화면에서만 본다.
// 숫자와 묶음은 고객지원이 코드로 센 값이고 화면이 새로 계산하지 않는다.

const REFRESH_MS = 30_000;
const DEFAULT_MIN = 3;
type Filter = "new" | "sent" | "all";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "new", label: "아직 안 넘김" }, { id: "sent", label: "기획에 넘김" }, { id: "all", label: "전체" },
];
const KIND_TONE: Record<string, string> = {
  event: "bg-amber-50 text-amber-700", feature: "bg-brand-50 text-brand-700", improvement: "bg-emerald-50 text-emerald-700",
  product: "bg-sky-50 text-sky-700", other: "bg-ink-100 text-ink-700",
};
const PERIODS: { id: IdeaPeriod; label: string; unit: string }[] = [
  { id: "week", label: "주별", unit: "주" }, { id: "month", label: "월별", unit: "월" }, { id: "year", label: "연별", unit: "연" },
];
const bucketTitle = (period: IdeaPeriod, start: string) =>
  period === "year" ? `${start.slice(0, 4)}년` : period === "month" ? `${start.slice(0, 4)}년 ${Number(start.slice(5, 7))}월` : `${dateStr(start)} 주`;

// 이벤트·혜택 요청을 하루 단위로 같은 뜻끼리 묶어 데이터에 넘긴 보고를 주·월·연으로 더한 순위.
// 합산은 데이터가 한 값이고 화면이 새로 계산하지 않는다. 보고에는 고객 원문이 없다.
function Trends() {
  const [period, setPeriod] = useState<IdeaPeriod>("week");
  const [data, setData] = useState<IdeaTrends | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    support.ideaTrends(period).then((env) => { if (live) { setData(env.data); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message || OFFLINE); });
    return () => { live = false; };
  }, [period]);
  return (
    <section className="card p-4" aria-label="이벤트·혜택 요청 순위">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[13px] font-semibold">이벤트·혜택 요청 순위</h2>
        <div className="flex gap-1.5" role="group" aria-label="기간">
          {PERIODS.map((p) => (
            <button key={p.id} type="button" aria-pressed={period === p.id} onClick={() => setPeriod(p.id)}
              className={`chip py-1 ${period === p.id ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{p.label}</button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-ink-500">
        날마다 고객지원이 같은 뜻으로 겹친 요청을 묶어 데이터 담당에게 넘기고, 데이터가 받은 보고를 기간별로 더한 순위입니다. 고객 원문은 넘어가지 않습니다.
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-red-800">{error}</p>}
      {!error && !data && <p className="mt-3 text-xs text-ink-500">불러오는 중…</p>}
      {data && data.buckets.length === 0 && <p className="mt-3 text-xs text-ink-500">{data.unknown_reason ?? "아직 받은 일일 보고가 없습니다."} 일일 보고는 하루가 지난 뒤 자동으로 넘어갑니다.</p>}
      {data && data.buckets.length > 0 && (
        <ul className="mt-3 grid gap-3 md:grid-cols-2">
          {data.buckets.map((b) => (
            <li key={b.start} className="rounded-xl bg-surface p-4 ring-1 ring-ink-100">
              <p className="text-sm font-semibold">{bucketTitle(period, b.start)}</p>
              <p className="mt-0.5 text-[11px] text-ink-500">{dateStr(b.start)} ~ {dateStr(b.end)} · 보고 {b.report_days}일 · 이벤트·혜택 요청 {b.total_ideas}건</p>
              {b.top.length === 0 ? <p className="mt-2 text-xs text-ink-500">겹친 요청이 없습니다.</p> : (
                <ol className="mt-2 space-y-1.5 text-xs">
                  {b.top.map((r, i) => (
                    <li key={r.key} className="flex items-center gap-2">
                      <span className="w-4 shrink-0 text-ink-500 tabular-nums">{i + 1}</span>
                      <span className="min-w-0 flex-1 truncate font-medium">{r.label}</span>
                      <span className="tabular-nums text-ink-700">{r.count}건</span>
                      <span className="w-24 shrink-0 text-right text-[11px] text-ink-500">{r.days}일 · 고객 {r.customer_count}명</span>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const groupKey = (g: { kind: string; area: string }) => `${g.kind}:${g.area}`;

function rowMessage(r: IdeaDigestResult["sent"][number]): string {
  const name = `아이디어 ${r.idea_count}건`;
  if (r.error) return `${name}: 기획에 닿지 못해 그대로 남겼습니다 (${r.error}). 잠시 뒤 다시 눌러 주세요.`;
  if (r.outcome === "linked_duplicate") return `${name}: 기획이 같은 요청을 이미 갖고 있어 ${r.duplicate_of ?? r.request_id}에 묶었습니다.`;
  return `${name}: 기획에 요청 ${r.request_id}를 만들었습니다.`;
}

export default function VoicePage() {
  const [data, setData] = useState<IdeaList | null>(null);
  const [filter, setFilter] = useState<Filter>("new");
  const [min, setMin] = useState(DEFAULT_MIN);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const env = await support.ideas({ minIdeas: min });
      setData(env.data); setError(null);
    } catch (e) { setError((e as Error).message || OFFLINE); }
  }, [min]);
  useEffect(() => {
    void load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  async function send(target: { kind?: IdeaGroup["kind"]; area?: IdeaGroup["area"] }, key: string) {
    setBusy(key); setError(null); setNotice([]);
    try {
      const out = await api.admin.sendIdeaDigest({ ...target, min_ideas: target.kind ? 1 : min });
      setNotice(out.sent.length ? out.sent.map(rowMessage) : [out.message ?? "넘길 만큼 쌓인 묶음이 없습니다."]);
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  if (!data) return <Loading error={error} />;
  const items: IdeaItem[] = data.items.filter((i) => filter === "all" || i.status === filter);
  const ready = data.groups.filter((g) => g.ready);
  const sentCount = data.items.filter((i) => i.status === "sent").length;

  return (
    <div className="space-y-5 px-8 pb-10 pt-2">
      <div>
        <h1 className="text-xl font-semibold">고객의 소리</h1>
        <p className="mt-1 text-sm text-ink-500">
          고객이 &quot;이런 걸 해 주면 좋겠다&quot;고 보낸 아이디어를 모읍니다. 불만·문의와는 따로 쌓이고, 환불·취소·불량 같은 문제는 아이디어로 받지 않고 문의로 안내합니다.
          같은 종류·영역의 아이디어가 쌓이면 골라서 기획에 넘기세요. 기획이 이벤트 요청이나 향후 방안 기획서로 이어 가고, 기획에는 고객 원문 없이 건수·고객 수·기간·많이 나온 말만 갑니다.
        </p>
      </div>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {notice.length > 0 && (
        <ul role="status" className="space-y-1 rounded-xl bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-800">{notice.map((n) => <li key={n}>{n}</li>)}</ul>
      )}

      <div className="flex flex-wrap gap-3">
        <Stat label="아직 안 넘긴 아이디어" value={data.new_count} sub="기획에 넘기기를 기다리는 건수" />
        <Stat label="넘길 만한 묶음" value={ready.length} tone={ready.length ? "text-amber-700" : ""} sub={`같은 묶음에 ${min}건 이상`} />
        <Stat label="기획에 넘긴 아이디어" value={sentCount} sub="현재 목록에서 센 값" />
      </div>

      <Trends />

      <section className="card p-4" aria-label="기획에 넘길 묶음">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-[13px] font-semibold">기획에 넘길 묶음</h2>
          <label className="flex items-center gap-1.5 text-xs text-ink-700">기준
            <select className="field py-0.5 text-xs" value={min} onChange={(e) => setMin(Number(e.target.value))} aria-label="넘길 기준 건수">
              {[1, 2, 3, 5, 10].map((n) => <option key={n} value={n}>{n}건 이상</option>)}
            </select>
          </label>
          <button className="btn-primary ml-auto text-xs" disabled={!humanTokenSet || ready.length === 0 || busy !== null} onClick={() => send({}, "all")}>
            {busy === "all" ? "넘기는 중…" : `기준 넘은 묶음 ${ready.length}개 모두 기획에 넘기기`}
          </button>
        </div>
        {!humanTokenSet && <p className="mt-2 text-xs text-amber-800">이 계정에는 결정 권한이 없어 넘기기 버튼이 꺼져 있습니다. 결정 권한이 있는 담당자 계정으로 들어오면 켜집니다.</p>}
        {data.groups.length === 0 ? (
          <p className="mt-3 text-xs text-ink-500">아직 쌓인 아이디어가 없습니다. 쇼핑몰에서 고객이 보낸 아이디어가 여기에 모입니다.</p>
        ) : (
          <ul className="mt-3 grid gap-3 md:grid-cols-2">
            {data.groups.map((g) => (
              <li key={groupKey(g)} className="rounded-xl bg-surface p-4 ring-1 ring-ink-100">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className={`chip ${KIND_TONE[g.kind] ?? KIND_TONE.other}`}>{g.kind_label}</span>
                  <span className="chip bg-ink-100 text-ink-700">{g.area_label}</span>
                  {g.ready ? <span className="chip bg-amber-50 text-amber-700">넘길 만큼 쌓임</span> : <span className="text-ink-500">더 쌓이면 넘기세요</span>}
                </div>
                <p className="mt-2 text-sm"><b className="tabular-nums">{g.idea_count}건</b> · 고객 {g.customer_count}명 · {dateStr(g.first_at)}{g.first_at.slice(0, 10) === g.last_at.slice(0, 10) ? "" : ` ~ ${dateStr(g.last_at)}`}</p>
                <p className="mt-1 text-xs text-ink-500">
                  {g.top_words.length ? `많이 나온 말: ${g.top_words.map((w) => `${w.word} ${w.count}`).join(", ")}` : "두 건 이상에 겹쳐 나온 말이 아직 없습니다."}
                </p>
                <button className="btn-ghost mt-3 py-1 text-xs" disabled={!humanTokenSet || busy !== null} onClick={() => send({ kind: g.kind, area: g.area }, groupKey(g))}>
                  {busy === groupKey(g) ? "넘기는 중…" : "이 묶음만 기획에 넘기기"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="아이디어 거르기">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
            className={`chip py-1 ${filter === f.id ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>
            {f.label} {f.id === "all" ? data.items.length : data.items.filter((i) => i.status === f.id).length}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <p className="text-xs text-ink-500">보여 줄 아이디어가 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((i) => (
            <li key={i.idea_id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className={`chip ${KIND_TONE[i.kind] ?? KIND_TONE.other}`}>{i.kind_label}</span>
                <span className="chip bg-ink-100 text-ink-700">{i.area_label}</span>
                <b className="font-mono">{i.idea_id}</b>
                <span className="text-ink-500">{dateStr(i.created_at)}</span>
                {i.status === "sent"
                  ? <span className="chip ml-auto bg-emerald-50 text-emerald-700">기획에 넘김 {i.request_id}</span>
                  : <span className="chip ml-auto bg-amber-50 text-amber-700">아직 안 넘김</span>}
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink-700">{i.text}</p>
              {i.status === "sent" && i.sent_by && <p className="mt-2 text-[11px] text-ink-500">넘긴 사람 {i.sent_by}{i.sent_at ? ` · ${dateStr(i.sent_at)}` : ""}</p>}
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-ink-500">목록에는 최근 아이디어 50건까지 보입니다. 고객 번호는 이 화면에 나오지 않습니다.</p>
    </div>
  );
}
