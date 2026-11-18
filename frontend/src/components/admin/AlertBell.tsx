import { useEffect, useRef, useState } from "react";
import { Link } from "@/lib/nav";
import { IconBell } from "../icons";
import { useDevAlerts, type DevAlert } from "@/lib/navCounts";

// 상단 종: 개발 AI 작업에서 사람이 봐야 하는 일 (CI 실패·머지 충돌·자동 머지 안 됨·리뷰 불일치 등).
// 목록은 사이드바 숫자와 같이 30초마다 개발 서버 상태에서 센다 (useNavCounts). 열어 본 알림은 이 브라우저에 기억해 숫자에서 뺀다.
const SEEN_KEY = "axcellent-alerts-seen";
const sig = (a: DevAlert) => `${a.id}|${a.why}`;
const readSeen = (): string[] => {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) ?? "[]") as string[]; } catch { return []; }
};
const writeSeen = (xs: string[]) => {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(xs)); } catch { /* 저장이 막혀도 이번 화면에서는 읽음으로 둔다 */ }
};

export function AlertBell() {
  const alerts = useDevAlerts();
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<string[]>(readSeen);
  const box = useRef<HTMLDivElement>(null);
  const fresh = alerts.filter((a) => !seen.includes(sig(a)));

  useEffect(() => {
    if (!open) return;
    // 연 순간 보이는 알림을 읽음으로 (지금 없는 알림 기록은 지운다)
    const now = alerts.map(sig);
    setSeen(now);
    writeSeen(now);
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open, alerts]);

  // 버튼 이름에는 배지에 보이는 글자("3", "9+")를 띄어 쓴 낱말 그대로 넣는다 (보이는 글자와 이름이 다르면 음성 명령·접근성 점검에서 걸린다)
  const badge = fresh.length > 9 ? "9+" : String(fresh.length);
  const label = fresh.length ? `새 알림 ${badge} 개` : "알림";
  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label}
        className="relative flex h-8 w-8 items-center justify-center rounded-full hover:bg-ink-100">
        <IconBell size={20} />
        {fresh.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold leading-none !text-white">
            {badge}
          </span>
        )}
      </button>
      <span className="sr-only" aria-live="polite">{fresh.length ? label : ""}</span>
      {open && (
        <div role="menu" className="card absolute right-0 top-11 z-50 w-80 border border-ink-100 p-2">
          <p className="px-3 py-2 text-xs font-semibold text-ink-500">개발 AI · 사람 확인 필요</p>
          {alerts.length === 0 ? (
            <p className="px-3 pb-3 text-sm text-ink-500">확인할 일이 없어요.</p>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {alerts.map((a) => (
                <li key={a.id}>
                  <Link role="menuitem" href={a.href} onClick={() => setOpen(false)} className="block rounded-xl px-3 py-2 hover:bg-ink-100">
                    <span className="block truncate text-sm font-medium text-ink-900">{a.id} {a.title}</span>
                    <span className="mt-0.5 block text-xs text-red-700">{a.why}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
