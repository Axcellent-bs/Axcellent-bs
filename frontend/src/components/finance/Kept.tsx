import { ago, shortDateTime } from "@/lib/format";
import type { SelfTest, SelfTestLastMeta } from "@/lib/finance";

// 재무 AI 테스트 결과. 서버가 10분마다 자동으로 테스트하고, 화면은 1분마다 최근 결과를 받아 보여 준다.
// 사람이 버튼을 누를 필요는 없다. "지금 다시 테스트"는 바로 확인하고 싶을 때만 쓴다.

/** 자동 테스트 간격(서버 meta.interval_seconds가 없으면 10분으로 본다) */
const DEFAULT_INTERVAL_S = 600;
/** 최근 결과가 이보다 오래되면 "늦어지고 있다"고 알린다. 간격의 2배, 최소 20분 */
export function staleAfterMs(meta: SelfTestLastMeta | null | undefined): number {
  const interval = meta?.interval_seconds && meta.interval_seconds > 0 ? meta.interval_seconds : DEFAULT_INTERVAL_S;
  return Math.max(20 * 60, interval * 2) * 1000;
}
export const isStale = (test: SelfTest | null, meta: SelfTestLastMeta | null | undefined) =>
  !test || Date.now() - new Date(test.ran_at).getTime() > staleAfterMs(meta);

const TEST_TONE = {
  pass: { box: "bg-emerald-50 ring-emerald-100 text-emerald-800", label: "정상" },
  warn: { box: "bg-amber-50 ring-amber-100 text-amber-800", label: "주의" },
  fail: { box: "bg-red-50 ring-red-100 text-red-800", label: "실패" },
} as const;

const TRIGGER_KO = { auto: "자동 테스트", manual: "직접 한 테스트" } as const;

function intervalKo(meta: SelfTestLastMeta | null | undefined) {
  const s = meta?.interval_seconds;
  if (!s || s <= 0) return null;
  return s % 60 === 0 ? `${s / 60}분마다` : `${s}초마다`;
}

/**
 * 재무 AI 테스트 상태.
 * - 결과가 없거나 오래됐으면 "늦어지고 있습니다" 경고
 * - 결과가 있으면 상태·언제(N분 전)·자동/직접·다음 자동 테스트 시각
 * `open`: 사람이 "지금 다시 테스트"를 누른 직후에는 항목을 펼쳐 보여 준다.
 */
export function SelfTestPanel({ test, meta, loaded, open, running, disabled, onRunNow }: {
  test: SelfTest | null;
  meta: SelfTestLastMeta | null | undefined;
  /** 한 번이라도 /self-test/last 응답을 받았는지 */
  loaded: boolean;
  open: boolean;
  running: boolean;
  disabled: boolean;
  onRunNow: () => void;
}) {
  const stale = loaded && isStale(test, meta);
  const runBtn = (
    <button type="button" className="btn-ghost text-xs" onClick={onRunNow} disabled={running || disabled}>
      {running ? "테스트하는 중…" : "지금 다시 테스트"}
    </button>
  );
  const every = intervalKo(meta);
  const next = meta?.next_run_at ? `다음 자동 테스트 ${shortDateTime(meta.next_run_at)}` : null;

  const staleNote = stale && (
    <p role="alert" className="flex flex-wrap items-center gap-2 rounded-2xl bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800 ring-1 ring-amber-100 sm:px-5">
      <span className="min-w-0 flex-1">
        자동 테스트 결과가 늦어지고 있습니다 (재무 AI 서버 상태 확인).
        <span className="block text-xs font-normal">
          {test ? `마지막 결과는 ${ago(test.ran_at)}입니다.` : "아직 받은 테스트 결과가 없습니다."} 서버는 {every ?? "10분마다"} 스스로 테스트합니다.
        </span>
      </span>
      {runBtn}
    </p>
  );

  if (!test) {
    if (!loaded) return null;
    return staleNote || null;
  }

  const tone = TEST_TONE[test.result] ?? TEST_TONE.warn;
  const bad = test.checks.filter((c) => !c.ok);
  return (
    <div className="space-y-2">
      {staleNote}
      <section aria-live="polite" aria-labelledby="self-test-title" className={`rounded-2xl px-4 py-4 ring-1 sm:px-5 ${tone.box}`}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 id="self-test-title" className="text-base font-semibold">재무 AI 테스트: {tone.label}</h2>
          <p className="min-w-0 flex-1 text-sm">{test.summary}</p>
          {!stale && runBtn}
        </div>
        <p className="mt-1 text-xs opacity-80">
          {ago(test.ran_at)} ({shortDateTime(test.ran_at)})
          {test.trigger && TRIGGER_KO[test.trigger] && ` · ${TRIGGER_KO[test.trigger]}`}
          {` · ${test.passed}/${test.total} 통과`}
          {test.duration_ms >= 100 && ` · ${(test.duration_ms / 1000).toFixed(1)}초`}
          {next && ` · ${next}`}
          {!next && every && ` · ${every} 자동 테스트`}
        </p>
        {test.result !== "pass" && (
          <p className="mt-1 text-xs font-medium">
            {test.result === "fail"
              ? "테스트를 통과하지 못했습니다. 문제가 해결될 때까지 재무 AI의 숫자와 리포트를 믿고 쓰지 마세요."
              : "연결 문제가 해결될 때까지 일부 숫자가 ‘확인할 수 없음’으로 보일 수 있습니다."}
          </p>
        )}
        {bad.length > 0 && (
          <ul className="mt-2 space-y-1 text-sm">
            {bad.map((c) => <li key={c.name}><b>{c.level === "fail" ? "실패" : "주의"}</b> · {c.name}: {c.detail}</li>)}
          </ul>
        )}
        <details className="mt-2 text-sm" open={open}>
          <summary className="cursor-pointer text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">점검한 항목 {test.total}개 모두 보기</summary>
          <ul className="mt-2 grid gap-1 sm:grid-cols-2">
            {test.checks.map((c) => (
              <li key={c.name} className="flex gap-2 rounded-lg bg-surface/70 px-2 py-1 text-ink-700">
                <span className={`chip shrink-0 ${c.ok ? "bg-emerald-50 text-emerald-700" : c.level === "fail" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"}`}>{c.ok ? "통과" : c.level === "fail" ? "실패" : "주의"}</span>
                <span><b className="text-ink-900">{c.name}</b> <span className="text-xs">{c.detail}</span></span>
              </li>
            ))}
          </ul>
        </details>
        <p className="mt-2 text-[11px] opacity-80">가짜 자료로 점검해서 실제 기록이나 돈에는 영향이 없습니다. 화면은 1분마다 최근 결과를 다시 받습니다.</p>
      </section>
    </div>
  );
}
