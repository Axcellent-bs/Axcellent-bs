import { useEffect, useState } from "react";
import { durationKo, segmentsOf, type EventTimes } from "@/lib/duration";
import { hr, type HrCard } from "@/lib/hr";

// 처음 겪은 장애와 반복된 장애(시작할 때 같은 유형의 과거 이벤트를 근거로 본 것)의 시간을 견준다.
// 시간은 시스템이 잰 값이다: 감지부터 쇼핑몰이 정상 복귀를 알린 때까지. 사람이 적은 값이 아니다.
// 건수가 적어서 통계가 아니라 사례 비교로만 읽는다. 3건 미만이면 가운데 값을 내지 않고 사례를 그대로 보여 준다.
interface Row { cardId: string; title: string; total: number; evidence: number | null; human: number | null; repeat: boolean }

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const MIN_FOR_MEDIAN = 3;

function rowsOf(cards: HrCard[]): Row[] {
  const out: Row[] = [];
  for (const c of cards) {
    const t = c.extra?.timeline as EventTimes | undefined;
    const seg = segmentsOf(t);
    if (!t || seg.total == null) continue; // 정상 복귀 시각이 있어야 잰 시간으로 센다
    out.push({ cardId: c.card_id, title: c.title, total: seg.total, evidence: seg.evidence, human: seg.human, repeat: !!t.prior_case_id });
  }
  return out;
}

export default function RepeatCompare() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      hr.decisionCards(undefined, 50)
        .then((cards) => { if (alive) { setRows(rowsOf(cards)); setErr(null); } })
        .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "기록을 받지 못했습니다"); });
    load();
    const poll = setInterval(load, 15000); // 정상 복귀가 방금 기록돼도 15초 안에 반영된다
    return () => { alive = false; clearInterval(poll); };
  }, []);

  const groups = [
    { label: "처음 겪은 장애", note: "시작할 때 같은 유형의 과거 이벤트가 없음", items: (rows ?? []).filter((r) => !r.repeat) },
    { label: "반복된 장애", note: "시작할 때 같은 유형의 과거 이벤트를 근거로 봄", items: (rows ?? []).filter((r) => r.repeat) },
  ];
  return (
    <section className="card space-y-3 p-6">
      <div>
        <h2 className="text-lg font-semibold">처음과 반복의 해결 시간</h2>
        <p className="mt-1 text-xs text-ink-500">시간은 이벤트를 감지한 때부터 쇼핑몰이 정상 복귀를 알린 때까지이고, 시스템이 쟀습니다. 정상 복귀가 기록된 이벤트만 셉니다. 건수가 적어 통계가 아니라 사례 비교로만 보세요. 시연에서는 장애를 해제한 시각이 복귀 시각이라 시연하는 사람의 속도입니다.</p>
      </div>
      {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}
      {!rows ? (!err && <p className="text-xs text-ink-500">불러오는 중…</p>) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {groups.map((g) => (
            <div key={g.label} className="rounded-2xl border border-ink-100 p-4">
              <p className="text-sm font-semibold">{g.label} <span className="font-normal text-ink-500">· {g.items.length}건</span></p>
              <p className="text-xs text-ink-500">{g.note}</p>
              {g.items.length === 0 ? <p className="mt-2 text-sm text-ink-500">아직 없습니다.</p> : (
                <>
                  {g.items.length >= MIN_FOR_MEDIAN && (
                    <p className="mt-2 text-2xl font-semibold tabular-nums">{durationKo(median(g.items.map((i) => i.total)))} <span className="text-xs font-normal text-ink-500">가운데 값</span></p>
                  )}
                  <ul className="mt-2 space-y-1.5 text-xs tabular-nums">
                    {g.items.map((i) => (
                      <li key={i.cardId} className="rounded-lg bg-ink-50 px-3 py-2">
                        <span className="block truncate font-medium" title={i.title}>{i.title}</span>
                        <span className="text-ink-700">감지부터 복귀까지 <b>{durationKo(i.total)}</b></span>
                        <span className="ml-2 text-ink-500">
                          {i.evidence != null && `원인 후보 ${durationKo(i.evidence)}`}
                          {i.human != null && ` · 사람에게 넘긴 뒤 ${durationKo(i.human)}`}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {g.items.length < MIN_FOR_MEDIAN && <p className="mt-1 text-xs text-ink-500">{MIN_FOR_MEDIAN}건 미만이라 가운데 값은 내지 않습니다.</p>}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
