import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MENU_WIDTH, menuHeight, menuPlace, type Place } from "@/lib/menuPlace";

// 방 목록 줄의 ⋯ 메뉴. 줄에 마우스를 올리거나 키보드로 오면 보인다. Esc·바깥 클릭으로 닫는다(닫으면 ⋯ 버튼으로 포커스).
// 11/23: 열린 메뉴는 document.body에 position: fixed로 그린다(createPortal). 방 줄 안에 absolute로 열면 다음 방 줄이
// 그 위에 그려지고, 방 목록 스크롤 상자가 메뉴를 잘라냈다. 위치는 ⋯ 버튼 기준(lib/menuPlace.ts), 아래가 좁으면 위로 연다.
// 스크롤(방 목록 상자 포함)·창 크기가 바뀌면 닫는다.

export interface MenuItem {
  key: string;
  label: string;
  sub?: string;
  disabled?: boolean;
  danger?: boolean;
  onSelect?: () => void;
}

export function RoomMenu({ title, items, onOpen, className = "right-2" }: {
  title: string; items: MenuItem[]; onOpen?: () => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const measure = (height: number) => {
    const r = btn.current?.getBoundingClientRect();
    if (r) setPlace(menuPlace(r, { width: window.innerWidth, height: window.innerHeight }, height, MENU_WIDTH));
  };

  // 그린 뒤 실제 메뉴 높이로 다시 잰다 (어림값과 다르면 위·아래 판정이 바뀔 수 있다)
  useLayoutEffect(() => {
    if (!open || !menu.current) return;
    const h = menu.current.offsetHeight;
    if (h) measure(h);
  }, [open, items.length]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!wrap.current?.contains(t) && !menu.current?.contains(t)) setOpen(false); // 포털 안 메뉴도 안쪽이다
    };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    const close = (e: Event) => { if (!menu.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    window.addEventListener("scroll", close, true); // 방 목록 상자의 스크롤도 잡는다(capture)
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  const toggle = () => {
    if (open) { setOpen(false); return; }
    onOpen?.();
    measure(menuHeight(items));
    setOpen(true);
  };

  return (
    <div ref={wrap} className={`absolute top-1/2 -translate-y-1/2 ${className}`}>
      <button ref={btn} type="button" aria-label={`${title} 메뉴`} aria-haspopup="menu" aria-expanded={open}
        onClick={toggle}
        className={`border border-ink-100 bg-surface px-1.5 text-[13px] font-bold leading-5 text-ink-700 transition hover:bg-ink-50 focus:opacity-100 group-hover:opacity-100 ${open ? "opacity-100" : "opacity-0"}`}>
        ⋯
      </button>
      {open && place && createPortal(
        <div ref={menu} role="menu" aria-label={`${title} 메뉴`} style={{ position: "fixed", top: place.top, left: place.left, width: MENU_WIDTH }}
          className="z-50 border border-ink-100 bg-surface py-1 shadow-lg">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem" disabled={it.disabled}
              onClick={() => { setOpen(false); it.onSelect?.(); }}
              className="block w-full px-3 py-1.5 text-left hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent">
              <span className={`block text-xs font-medium ${it.danger ? "text-red-700" : "text-ink-900"}`}>{it.label}</span>
              {it.sub && <span className="block text-[11px] text-ink-500">{it.sub}</span>}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
