import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import type { Envelope, FinanceMeta } from "@/lib/finance";

// 재무 화면 공통 부품: 칩, 예시 데이터 표시, 불러오기·오류·빈 상태, 섹션 틀, 데이터 훅.

export const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

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
  return <span className="chip bg-violet-50 text-violet-700" title="실제 쇼핑몰 자료가 아니라 연습용 예시 자료입니다">예시 데이터</span>;
}

/** 받은 시각과 못 받은 값 안내 */
export function MetaLine({ meta }: { meta: FinanceMeta | null | undefined }) {
  if (!meta) return null;
  return (
    <p className="text-[11px] text-ink-500">
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
    <details className="mt-1 text-[11px] text-ink-500">
      <summary className="cursor-pointer select-none hover:text-ink-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">{label} ({ids.length})</summary>
      <ul className="mt-1 flex flex-wrap gap-1">
        {ids.map((r) => {
          const word = humanId(r, sellers);
          return (
            <li key={r} className="rounded bg-ink-100 px-1.5 py-0.5">
              {word === r ? <code className="font-mono">{r}</code> : <>{word} <code className="font-mono text-[10px] text-ink-500">({r})</code></>}
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
export function Panel({ id, title, titleHidden, hint, meta, tools, children }: { id?: string; title: string; titleHidden?: boolean; hint?: ReactNode; meta?: FinanceMeta | null; tools?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined} className="card scroll-mt-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <h2 id={id ? `${id}-title` : undefined} className="flex flex-wrap items-center gap-2 text-base font-semibold text-ink-900">
            <span className={titleHidden ? "sr-only" : undefined}>{title}</span> <MockBadge meta={meta} />
          </h2>
          {hint && <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-ink-500">{hint}</p>}
          <MetaLine meta={meta} />
        </div>
        {tools && <div className="flex flex-wrap items-center gap-2">{tools}</div>}
      </div>
      <div className="mt-4">{children}</div>
    </section>
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
