import { useState } from "react";
import { Bars, Stat } from "@/components/admin/bits";
import { ChartWithTable, TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { Link } from "@/lib/nav";
import { shortDateTime } from "@/lib/format";
import { CHANGE_STATUS_KO, TOPIC_KO, ko, support, type ChangeStatus } from "@/lib/support";

// 문의 급증: 최근 창의 분류별 문의 수를 평소와 견준다. 변경 요청: 문의에서 기획개발로 보낸 요청과 처리 상태.

function Surge({ tick }: { tick: number }) {
  const res = useResource("inquiry-surge", () => support.inquirySurge(), tick);
  if (!res.env) return res.error ? <ErrorBox message={`문의 급증을 받지 못했습니다. ${res.error}`} onRetry={res.reload} /> : <Waiting />;
  const s = res.env.data;
  const bars: Record<string, number> = {};
  s.surges.forEach((x) => { bars[ko(TOPIC_KO, x.topic_code)] = x.count; });
  return (
    <Panel id="support-surge" title="문의 급증" meta={res.env.meta}
      hint={`최근 ${s.window_minutes}분 문의를 지난 ${s.baseline_hours}시간의 같은 길이 평균과 견줍니다.${s.evaluated_at ? ` ${shortDateTime(s.evaluated_at)} 기준.` : ""}`}>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      <div className="space-y-4">
        <div className="flex flex-wrap gap-3">
          <Stat label={`최근 ${s.window_minutes}분 문의`} value={`${s.total_in_window}건`} />
          <Stat label="급증한 분류" value={`${s.surges.length}개`} tone={s.surges.length ? "text-red-600" : ""} />
        </div>
        {s.surges.length === 0 ? <Empty>평소보다 눈에 띄게 늘어난 분류가 없습니다.</Empty> : (
          <>
            <div>
              <p className="mb-2 text-xs font-medium text-ink-700">분류별 최근 문의 (건)</p>
              <ChartWithTable
                chart={<Bars data={bars} color="bg-red-500" />}
                table={<TableView columns={["분류", "최근 문의", "평소 평균", "평소의 몇 배", "예시 문의"]} rows={s.surges.map((x) => [
                  ko(TOPIC_KO, x.topic_code), `${x.count}건`, `${x.baseline_per_window}건`, x.ratio == null ? "평소 없음" : `${x.ratio}배`,
                  x.sample_inquiry_ids.map((i) => `#${i}`).join(", ") || "–",
                ])} />}
              />
            </div>
            <ul className="space-y-2">
              {s.surges.map((x) => (
                <li key={x.topic_code} className="flex flex-wrap items-center gap-2 rounded-2xl bg-surface-2 px-4 py-3 text-sm">
                  <span className="font-semibold">{ko(TOPIC_KO, x.topic_code)}</span>
                  <Chip label={x.ratio == null ? "평소 없음" : `평소의 ${x.ratio}배`} tone={TONE.bad} />
                  <span className="ml-auto flex flex-wrap gap-2 text-xs">
                    {x.sample_inquiry_ids.map((i) => <Link key={i} href={`/support?inquiry=${i}#inquiry`} className="text-brand-600 underline">문의 #{i}</Link>)}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Panel>
  );
}

const FILTERS: { v: ChangeStatus | ""; label: string }[] = [
  { v: "", label: "전체" }, { v: "requested", label: "요청됨" }, { v: "completed", label: "처리 완료" }, { v: "declined", label: "반려" },
];

function Changes({ tick }: { tick: number }) {
  const [status, setStatus] = useState<ChangeStatus | "">("");
  const res = useResource(`changes-${status}`, () => support.changeRequests({ status: status || undefined }), tick);
  return (
    <Panel id="support-changes" title="변경 요청" meta={res.env?.meta}
      hint="문의를 받다가 상품·화면을 고쳐야 한다고 판단해 기획개발에 보낸 요청입니다. 보내는 일은 이 화면에서 하지 않습니다."
      tools={
        <div role="group" aria-label="처리 상태" className="flex gap-1 rounded-full bg-surface p-1 ring-1 ring-ink-100">
          {FILTERS.map((f) => (
            <button key={f.v} type="button" aria-pressed={status === f.v} onClick={() => setStatus(f.v)}
              className={`rounded-full px-3 py-1 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${status === f.v ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>{f.label}</button>
          ))}
        </div>
      }>
      {!res.env ? (res.error ? <ErrorBox message={`변경 요청을 받지 못했습니다. ${res.error}`} onRetry={res.reload} /> : <Waiting />) : (
        <>
          {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
          {res.env.data.length === 0 ? <Empty>해당하는 변경 요청이 없습니다.</Empty> : (
            <ul className="space-y-2">
              {res.env.data.map((c) => {
                const st = CHANGE_STATUS_KO[c.status] ?? { label: c.status, tone: "muted" as const };
                return (
                  <li key={`${c.context_id}-${c.seq}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-surface-2 px-4 py-3 text-sm">
                    <Chip label={st.label} tone={TONE[st.tone]} />
                    {c.inquiry_id != null ? <Link href={`/support?inquiry=${c.inquiry_id}#inquiry`} className="font-semibold text-brand-600 underline">문의 #{c.inquiry_id}</Link> : <span className="text-ink-500">문의 없음</span>}
                    {c.request_id && <span className="text-xs text-ink-500">요청 {c.request_id}</span>}
                    {c.duplicate_of && <Chip label="중복 요청" tone={TONE.muted} />}
                    <span className="ml-auto text-xs tabular-nums text-ink-500">{shortDateTime(c.updated_at ?? c.created_at)} 갱신</span>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

export function SurgeChanges({ tick }: { tick: number }) {
  return (
    <div className="space-y-5">
      <Surge tick={tick} />
      <Changes tick={tick} />
    </div>
  );
}
