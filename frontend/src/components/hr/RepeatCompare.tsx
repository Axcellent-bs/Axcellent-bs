import { useEffect, useState } from "react";
import { durationKo, secondsOf, type ResolutionTimes } from "@/lib/duration";
import { hr, type HrCard } from "@/lib/hr";

// 처음 겪은 장애와 반복된 장애(시작할 때 비슷한 과거 이벤트를 근거로 본 것)의 해결 시간을 나란히 본다.
// 사람이 해결 내용을 적어 시간이 잰 이벤트만 센다. 건수가 적어서 통계가 아니라 사례 비교로만 읽는다.
interface Row { cardId: string; title: string; seconds: number; repeat: boolean }

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

function rowsOf(cards: HrCard[]): Row[] {
  const out: Row[] = [];
  for (const c of cards) {
    const r = c.extra?.resolution as (ResolutionTimes & { outcome?: string; prior_case_id?: string | null }) | undefined;
    const seconds = secondsOf(r);
    if (!r || (r.outcome !== "resolved" && r.outcome !== "workaround") || seconds == null) continue;
    out.push({ cardId: c.card_id, title: c.title, seconds, repeat: !!r.prior_case_id });
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
    const poll = setInterval(load, 15000); // 해결 내용을 방금 적어도 15초 안에 반영된다
    return () => { alive = false; clearInterval(poll); };
  }, []);

  const groups = [
    { label: "처음 겪은 장애", note: "시작할 때 근거로 본 과거 이벤트가 없음", items: (rows ?? []).filter((r) => !r.repeat) },
    { label: "반복된 장애", note: "시작할 때 비슷한 과거 이벤트를 근거로 봄", items: (rows ?? []).filter((r) => r.repeat) },
  ];
  return (
    <section className="card space-y-3 p-6">
      <div>
        <h2 className="text-lg font-semibold">처음과 반복의 해결 시간</h2>
        <p className="mt-1 text-xs text-ink-500">사람이 해결 내용을 적은 이벤트만 셉니다. 시간은 이벤트가 열린 때부터 적은 때까지입니다. 건수가 적어 통계가 아니라 사례 비교로만 보세요.</p>
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
                  <p className="mt-2 text-2xl font-semibold tabular-nums">{durationKo(median(g.items.map((i) => i.seconds)))} <span className="text-xs font-normal text-ink-500">가운데 값</span></p>
                  <p className="text-xs text-ink-500 tabular-nums">가장 짧음 {durationKo(Math.min(...g.items.map((i) => i.seconds)))} · 가장 김 {durationKo(Math.max(...g.items.map((i) => i.seconds)))}</p>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
