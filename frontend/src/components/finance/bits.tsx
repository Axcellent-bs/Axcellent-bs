import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import type { AppliedRule, Envelope, FinanceMeta } from "@/lib/finance";

// 재무 화면 공통 부품: 칩, 예시 데이터 표시, 불러오기·오류·빈 상태, 섹션 틀, 데이터 훅.

export const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip text-sm ${tone}`}>{label}</span>;

export const TONE = {
  ok: "bg-emerald-50 text-emerald-700",
  warn: "bg-amber-50 text-amber-700",
  bad: "bg-red-50 text-red-700",
  info: "bg-brand-50 text-brand-700",
  muted: "bg-ink-100 text-ink-500",
} as const;

/** 목 모드 응답이면 "예시 데이터" 표시 */
export function MockBadge({ meta }: { meta: FinanceMeta | null | undefined }) {
  if (!meta?.mock) return null;
  return <span className="chip bg-violet-50 text-sm text-violet-700" title="실제 쇼핑몰 자료가 아니라 연습용 예시 자료입니다">예시 데이터</span>;
}

/** 받은 시각과 못 받은 값 안내 */
export function MetaLine({ meta }: { meta: FinanceMeta | null | undefined }) {
  if (!meta) return null;
  return (
    <p className="text-sm text-ink-500">
      {meta.collected_at && `${shortDateTime(meta.collected_at)} 기준`}
      {meta.unavailable && meta.unavailable.length > 0 && ` · 받지 못한 값 ${meta.unavailable.length}개는 "확인할 수 없음"으로 표시합니다`}
    </p>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">
      <span className="min-w-0 flex-1">{message}</span>
      {onRetry && <button className="btn-ghost" onClick={onRetry}>다시 시도</button>}
    </div>
  );
}

export const Empty = ({ children }: { children: ReactNode }) => (
  <p className="rounded-xl border border-dashed border-ink-300 px-4 py-6 text-center text-sm text-ink-500">{children}</p>
);

export const Waiting = ({ what = "불러오는 중…" }: { what?: string }) => (
  <p className="py-8 text-center text-sm text-ink-500" aria-live="polite">{what}</p>
);

/**
 * 관련 기록 번호. 평소에는 접어 두고, 직원이 "기록 번호 보기"를 누르면 "쿠폰 3053 (cpn_3053)"처럼 보여 준다.
 * `sellers`: 판매자 번호 → 이름
 */
export function RefList({ refs, sellers, label = "기록 번호 보기" }: { refs: (string | null | undefined)[]; sellers?: Record<string, string>; label?: string }) {
  const ids = [...new Set(refs.filter((r): r is string => !!r))];
  if (!ids.length) return null;
  return (
    <details className="mt-1 text-sm text-ink-500">
      <summary className="cursor-pointer select-none hover:text-ink-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">{label} ({ids.length})</summary>
      <ul className="mt-1 flex flex-wrap gap-1">
        {ids.map((r) => {
          const word = humanId(r, sellers);
          return (
            <li key={r} className="rounded bg-ink-100 px-1.5 py-0.5">
              {word === r ? <code className="font-mono">{r}</code> : <>{word} <code className="font-mono text-sm text-ink-500">({r})</code></>}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

/**
 * 섹션 틀: 제목·설명·오른쪽 도구 + 본문.
 * `titleHidden`: 바로 위 탭 이름과 같은 제목이면 화면에서는 숨기고(읽기 프로그램에는 남김) 한 번만 보이게 한다.
 */
/** 이 숫자를 계산한 회계 기준. 세무사·회계사가 아직 확인하지 않은 기준은 표시한다. 길어서 접어 둔다 */
export function AppliedRules({ rules }: { rules?: AppliedRule[] }) {
  if (!rules?.length) return null;
  const pending = rules.filter((r) => r.review === "pending").length;
  return (
    <details className="rounded-2xl border border-ink-100 px-4 py-2 text-sm">
      <summary className="cursor-pointer font-medium text-ink-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
        이 숫자를 계산한 회계 기준 {rules.length}개
        {pending > 0 && <span className="font-normal text-ink-500"> (세무사·회계사 확인 전 {pending}개)</span>}
      </summary>
      <p className="mt-2 text-ink-500">재무 AI가 이 기준대로 계산했어요. 아직 세무사·회계사가 확인하지 않은 기준은 '확인 전'으로 표시해요.</p>
      <ul className="mt-2 space-y-2">
        {rules.map((r) => (
          <li key={r.rule_id}>
            <p className="flex flex-wrap items-center gap-2 font-medium text-ink-900">
              {r.title}
              <Chip label={r.rule_id} tone={TONE.muted} />
              {r.review === "pending" && <Chip label="세무사·회계사 확인 전" tone={TONE.warn} />}
            </p>
            <p className="text-ink-700">{r.statement}</p>
            {r.detail && <p className="text-ink-700">이번 판단: {r.detail}</p>}
            <p className="text-ink-500">근거: {r.basis}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}

// 금액·건수 같은 숫자(단위 포함)와 바로 앞 낱말. 예: "매출 80,533,700원", "주문 3,248건", "12.5%"
const AMOUNT = /(\S+\s)?([−+-]?\d[\d,]*(?:\.\d+)?\s?(?:억\s?)?(?:만\s?)?(?:원|건|%|개|명|배))/g;

/** AI가 쓴 문장은 그대로 두고 숫자와 그 앞 낱말만 진하게 보여 준다 (숫자는 표가 기준) */
export function EmphasizedNumbers({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(AMOUNT)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    const [, label, amount] = m;
    // 앞 숫자 바로 뒤에 붙은 조사("원이고,")는 낱말이 아니라서 진하게 하지 않는다
    if (label) parts.push(at === last && at > 0 ? label : <span key={`l${at}`} className="font-medium text-ink-900">{label}</span>);
    parts.push(<span key={`n${at}`} className="font-semibold tabular-nums text-ink-900">{amount}</span>);
    last = at + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

export function Panel({ id, title, titleHidden, hint, meta, tools, children }: { id?: string; title: string; titleHidden?: boolean; hint?: ReactNode; meta?: FinanceMeta | null; tools?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined} className="card scroll-mt-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          {/* 제목 줄: 제목 · 예시 표시 · 기준 시각(작게). 설명은 한 단 아래 읽기 좋은 폭으로만 */}
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h2 id={id ? `${id}-title` : undefined} className="flex flex-wrap items-center gap-2 text-base font-semibold text-ink-900">
              <span className={titleHidden ? "sr-only" : undefined}>{title}</span> <MockBadge meta={meta} />
            </h2>
            <MetaTime meta={meta} />
          </div>
          {hint && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-ink-500">{hint}</p>}
          <MetaMissing meta={meta} />
        </div>
        {tools && <div className="flex flex-wrap items-center gap-2">{tools}</div>}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** 제목 옆에 붙는 작은 기준 시각 */
function MetaTime({ meta }: { meta: FinanceMeta | null | undefined }) {
  if (!meta?.collected_at) return null;
  return <span className="text-xs tabular-nums text-ink-500">{shortDateTime(meta.collected_at)} 기준</span>;
}

/** 받지 못한 값이 있을 때만 한 줄 */
function MetaMissing({ meta }: { meta: FinanceMeta | null | undefined }) {
  if (!meta?.unavailable?.length) return null;
  return <p className="mt-1 text-sm text-amber-800">받지 못한 값 {meta.unavailable.length}개는 "확인할 수 없음"으로 표시합니다</p>;
}

// ---------------------------------------------------------------- 숫자 표시 부품
/**
 * 변동 표시: ▲ 1,234원 (+12%). `good`은 늘어나는 게 좋은 값인지(매출 true, 비용 false).
 * 좋은 쪽 변동은 초록, 나쁜 쪽은 빨강, 방향을 모르면 회색이다.
 */
export function Delta({ diff, pct, unit = "won", good, className = "" }: { diff: number | null | undefined; pct?: number | null; unit?: "won" | "count" | "pp"; good?: boolean; className?: string }) {
  if (diff == null || !Number.isFinite(diff)) return <span className={`text-sm text-ink-500 ${className}`}>변동 확인할 수 없음</span>;
  if (diff === 0) return <span className={`text-sm tabular-nums text-ink-500 ${className}`}>변동 없음</span>;
  const up = diff > 0;
  const tone = good == null ? "text-ink-700" : up === good ? "text-emerald-700" : "text-red-700";
  const abs = Math.abs(diff);
  const amount = unit === "won" ? `${Math.round(abs).toLocaleString("ko-KR")}원` : unit === "count" ? `${Math.round(abs).toLocaleString("ko-KR")}건` : `${(abs * 100).toFixed(1)}%p`;
  const rate = pct != null && Number.isFinite(pct) && unit !== "pp" ? `${up ? "+" : "−"}${Math.abs(pct * 100) >= 100 ? Math.round(Math.abs(pct * 100)).toLocaleString("ko-KR") : Math.abs(pct * 100).toFixed(1)}%` : null;
  return (
    <span className={`inline-flex flex-wrap items-baseline gap-x-1.5 text-sm tabular-nums ${tone} ${className}`}>
      <span aria-hidden="true">{up ? "▲" : "▼"}</span>
      <span className="sr-only">{up ? "늘어남" : "줄어듦"}</span>
      <span className="font-medium">{amount}</span>
      {rate && <span className="opacity-80">{rate}</span>}
    </span>
  );
}

/** 큰 숫자 하나: 이름표 · 값 · (비교 기준 값) · 변동. `lead`면 화면에서 가장 중요한 숫자로 크게 */
export function Stat({ label, value, sub, delta, lead = false, flag }: { label: ReactNode; value: ReactNode; sub?: ReactNode; delta?: ReactNode; lead?: boolean; flag?: string }) {
  return (
    <div className={`rounded-2xl border px-4 py-3 ${lead ? "border-brand-100 bg-brand-50/40" : "border-ink-100 bg-surface"}`}>
      <p className="flex items-center gap-1.5 text-sm text-ink-500">
        {flag && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-700" aria-hidden="true" />}
        {label}
        {flag && <span className="sr-only">{flag}</span>}
      </p>
      <p className={`mt-1 break-words font-semibold tabular-nums text-ink-900 ${lead ? "text-2xl" : "text-xl"}`}>{value}</p>
      {delta && <div className="mt-1">{delta}</div>}
      {sub && <p className="mt-0.5 text-sm tabular-nums text-ink-500">{sub}</p>}
    </div>
  );
}

/** 한 값을 몇 갈래로 나눈 크기: 줄마다 이름 · 막대 · 값 · 비중 (음수도 크기로 그린다) */
export function ShareBars({ rows, format }: { rows: { label: string; value: number }[]; format: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
  const total = rows.reduce((t, r) => t + Math.abs(r.value), 0) || 1;
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(7rem,10rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="text-ink-700">{r.label}</span>
          <span className="h-2 overflow-hidden rounded-full bg-ink-100" aria-hidden="true">
            <span className={`block h-full rounded-full ${r.value < 0 ? "bg-red-700/60" : "bg-brand-500"}`} style={{ width: `${Math.max((Math.abs(r.value) / max) * 100, 1)}%` }} />
          </span>
          <span className="tabular-nums text-ink-900">{format(r.value)} <span className="text-ink-500">{Math.round((Math.abs(r.value) / total) * 100)}%</span></span>
        </li>
      ))}
    </ul>
  );
}

export interface Resource<T> {
  env: Envelope<T> | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  /** 쓰기 응답으로 화면 값을 바로 바꾼다 */
  set: (fn: (cur: Envelope<T>) => Envelope<T>) => void;
}

/**
 * `key`가 바뀌거나 `tick`이 오르면 다시 받는다. 다시 받는 동안 이전 값은 그대로 보여 준다.
 * 늦게 도착한 옛 응답은 버린다.
 */
export function useResource<T>(key: string, load: () => Promise<Envelope<T>>, tick = 0): Resource<T> {
  const [env, setEnv] = useState<Envelope<T> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  const seq = useRef(0);
  useEffect(() => { loadRef.current = load; });

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const next = await loadRef.current();
      if (mine !== seq.current) return;
      setEnv(next);
      setError(null);
    } catch (e) {
      if (mine !== seq.current) return;
      setError((e as Error).message);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void reload(); }, [key, tick, reload]);
  const set = useCallback((fn: (cur: Envelope<T>) => Envelope<T>) => setEnv((cur) => (cur ? fn(cur) : cur)), []);
  return { env, error, loading, reload, set };
}

/** 리소스 상태에 맞춰 불러오는 중 / 오류 / 본문을 고른다. 값이 있으면 오류는 위에 작게 붙인다 */
export function ResourceBody<T>({ res, children }: { res: Resource<T>; children: (data: T) => ReactNode }) {
  if (!res.env) return res.error ? <ErrorBox message={res.error} onRetry={res.reload} /> : <Waiting />;
  return (
    <>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      {children(res.env.data)}
    </>
  );
}
