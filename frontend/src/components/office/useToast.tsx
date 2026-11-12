import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

// 화면 아래에 잠깐 뜨는 알림 한 줄. 되돌리기 같은 버튼을 하나 달 수 있다.
// 쓰는 곳: `const toast = useToast();` → `toast.show("글")` 하고 `{toast.node}`를 화면 어딘가에 둔다.

export interface ToastOpts {
  action?: { label: string; onClick: () => void };
  /** 떠 있는 시간(ms). 기본 4초 */
  ms?: number;
}

export interface ToastApi {
  show: (text: string, opts?: ToastOpts) => void;
  hide: () => void;
  node: ReactNode;
}

export function useToast(): ToastApi {
  const [t, setT] = useState<{ text: string; action?: ToastOpts["action"] } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const clear = useCallback(() => {
    if (timer.current !== undefined) window.clearTimeout(timer.current);
    timer.current = undefined;
  }, []);
  const hide = useCallback(() => { clear(); setT(null); }, [clear]);
  const show = useCallback((text: string, opts: ToastOpts = {}) => {
    clear();
    setT({ text, action: opts.action });
    timer.current = window.setTimeout(() => setT(null), opts.ms ?? 4000);
  }, [clear]);
  useEffect(() => clear, [clear]);

  const node = t ? (
    <div role="status" aria-live="polite" className="fixed bottom-4 left-1/2 z-[60] flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-3 bg-ink-900 px-4 py-2.5 text-sm text-app shadow-lg">
      <span className="min-w-0">{t.text}</span>
      {t.action && (
        <button type="button" onClick={() => { t.action?.onClick(); hide(); }} className="shrink-0 font-bold underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
          {t.action.label}
        </button>
      )}
    </div>
  ) : null;
  return { show, hide, node };
}

/** 지우기 실패 문구: `삭제하지 못했어요 (<상태>) <이유>` */
export function removeFailText(err: unknown): string {
  const status = typeof err === "object" && err !== null && "status" in err && typeof err.status === "number" ? String(err.status) : "연결 실패";
  const reason = err instanceof Error ? err.message : "";
  return `삭제하지 못했어요 (${status}) ${reason}`.trim();
}
