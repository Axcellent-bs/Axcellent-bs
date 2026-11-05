import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/nav";
import { api } from "@/lib/api";
import { LANGUAGE_KO, URGENCY_DOT } from "@/lib/labels";
import {
  STAGE_KO, cardsFromConversations, isDone, replayCards, replayLength, workerLine,
  type BoardCard, type Stage,
} from "@/lib/supportBoard";

// 문의 현황의 업무 보드. 고객지원 AI·상담원이 일하는 모습을 왼쪽(처리할 일) → 오른쪽(처리된 일)으로 보여 준다.
// 실제 모드: 상담 목록의 지금 상태. 재생 모드: 같은 목록을 처음부터 처리하는 모습 (영상·설명용). 어느 쪽도 데이터를 지어내지 않는다.

type Mode = "live" | "replay";
const POLL_MS = 5000;
const REPLAY_MS = 1600;

const STAGE_LOOK: Record<Stage, string> = {
  new: "bg-ink-100 text-ink-700",
  drafting: "bg-brand-50 text-brand-700",
  review: "bg-amber-50 text-amber-700",
  sent: "bg-emerald-50 text-emerald-700",
  resolved: "bg-emerald-50 text-emerald-700",
  escalated: "bg-ink-100 text-ink-700",
};

export function SupportBoard() {
  const [mode, setMode] = useState<Mode>("live");
  const [real, setReal] = useState<BoardCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [paused, setPaused] = useState(false);

  // 실제 목록: 실제 모드에서만 5초마다 새로 받는다. 재생 중에는 한 번 받아 둔 목록을 쓴다
  useEffect(() => {
    let alive = true;
    const load = () => api.admin.list()
      .then((l) => { if (alive) { setReal(cardsFromConversations(l)); setError(null); } })
      .catch(() => { if (alive) setError("문의 목록을 불러올 수 없습니다. 백엔드가 켜져 있는지 확인하세요."); });
    load();
    if (mode !== "live") return () => { alive = false; };
    const t = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [mode]);

  useEffect(() => {
    if (mode !== "replay" || paused || !real?.length) return;
    const t = setInterval(() => setTick((x) => (x + 1) % replayLength(real.length)), REPLAY_MS);
    return () => clearInterval(t);
  }, [mode, paused, real]);

  const cards = useMemo(() => (real ? (mode === "replay" ? replayCards(real, tick) : real) : []), [real, mode, tick]);
  const todo = cards.filter((c) => !isDone(c.stage));
  const done = cards.filter((c) => isDone(c.stage));
  const line = workerLine(cards);

  const switchTo = (m: Mode) => { setMode(m); setTick(0); setPaused(false); };

  return (
    <section aria-label="고객지원 업무 보드">
      <style>{`@keyframes board-in{from{opacity:0;transform:translateX(-28px)}to{opacity:1;transform:none}}
.board-card{animation:board-in .5s ease-out}@media (prefers-reduced-motion:reduce){.board-card{animation:none}}`}</style>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">고객지원 업무 보드</h2>
        <div className="ml-auto flex items-center gap-2 text-xs">
          {mode === "replay" && (
            <button onClick={() => setPaused((p) => !p)} className="rounded-full bg-ink-50 px-3 py-1.5 font-medium text-ink-700 hover:bg-ink-100">{paused ? "재생" : "멈춤"}</button>
          )}
          {(["live", "replay"] as const).map((m) => (
            <button key={m} onClick={() => switchTo(m)} aria-pressed={mode === m}
              className={`rounded-full px-3 py-1.5 font-medium ${mode === m ? "bg-accent text-white" : "bg-ink-50 text-ink-700 hover:bg-ink-100"}`}>
              {m === "live" ? "실제 모드" : "처리 과정 재생"}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-3 grid gap-2 text-xs sm:grid-cols-2">
        <Worker name="고객지원 AI" doing={line.ai} active={cards.some((c) => c.worker === "ai")} />
        <Worker name="상담원" doing={line.human} active={cards.some((c) => c.worker === "human")} />
      </div>

      {error && !real ? <p className="card px-4 py-6 text-center text-sm text-ink-500">{error}</p> : !real?.length ? (
        <p className="card px-4 py-6 text-center text-sm text-ink-500">{real ? "아직 문의가 없습니다. 새 문의가 들어오면 이 보드에 나타납니다." : "문의 목록을 불러오는 중…"}</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <Column title="처리할 일" hint="새 문의 · AI 작성 · 상담원 확인" cards={todo} />
          <Column title="처리된 일" hint="답변 · 해결 · 이관" cards={done} />
        </div>
      )}
      {mode === "replay" && real?.length ? (
        <p className="mt-2 text-[11px] text-ink-500">처리 과정 재생은 지금 있는 실제 문의를 처음부터 처리하는 모습으로 다시 보여 줍니다. 실제 상태는 바뀌지 않습니다.</p>
      ) : null}
    </section>
  );
}

function Worker({ name, doing, active }: { name: string; doing: string; active: boolean }) {
  return (
    <div className="card flex items-center gap-3 px-4 py-3">
      <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${active ? "bg-brand-500" : "bg-ink-300"}`} />
      <div className="min-w-0"><p className="font-semibold">{name} <span className="font-normal text-ink-500">· {active ? "작업 중" : "대기"}</span></p>
        <p className="truncate text-ink-500">{doing}</p></div>
    </div>
  );
}

function Column({ title, hint, cards }: { title: string; hint: string; cards: BoardCard[] }) {
  return (
    <div className="bg-surface-2 p-3">
      <div className="mb-2 flex items-baseline gap-2"><h3 className="text-sm font-semibold">{title}</h3><span className="text-[11px] tabular-nums text-ink-500">{cards.length}건</span>
        <span className="ml-auto text-[11px] text-ink-500">{hint}</span></div>
      <ul className="space-y-2">
        {cards.map((c) => <li key={`${c.id}-${c.stage}`} className="board-card"><Card c={c} /></li>)}
        {!cards.length && <li className="py-4 text-center text-xs text-ink-500">비어 있습니다.</li>}
      </ul>
    </div>
  );
}

function Card({ c }: { c: BoardCard }) {
  return (
    <Link href={`/console?id=${c.id.slice(1)}`} className="block rounded-xl bg-surface p-3 hover:ring-1 hover:ring-brand-500">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`h-2 w-2 rounded-full ${URGENCY_DOT[c.urgency ?? "low"] ?? "bg-ink-300"}`} />
        <b>{c.who}</b>
        {c.lang !== "ko" && <span className="chip bg-amber-50 text-amber-700">{LANGUAGE_KO[c.lang] ?? LANGUAGE_KO.other}</span>}
        <span className={`chip ml-auto ${STAGE_LOOK[c.stage]}`}>{STAGE_KO[c.stage]}</span>
      </div>
      <p className="mt-1 truncate text-sm font-medium">{c.subject}</p>
    </Link>
  );
}
