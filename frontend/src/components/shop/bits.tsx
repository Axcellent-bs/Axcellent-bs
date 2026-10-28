import { Link } from "@/lib/nav";
import { useEffect, type ReactNode } from "react";
import { won } from "@/lib/format";
import type { Product } from "@/lib/types";
import { IconStar } from "../icons";
import { Cover } from "./Cover";

export function Stars({ value, size = 14 }: { value: number | null; size?: number }) {
  const v = value ?? 0;
  return (
    <span className="inline-flex items-center gap-0.5 text-amber-700" aria-label={value == null ? "평점 없음" : `평점 ${value}점`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={i <= Math.round(v) ? "" : "opacity-25"}><IconStar size={size} fill="currentColor" /></span>
      ))}
    </span>
  );
}

export function Price({ p, big = false }: { p: Pick<Product, "list_price" | "sale_price" | "discount_pct">; big?: boolean }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      {p.discount_pct > 0 && <span className={`font-bold text-accent ${big ? "text-xl" : "text-sm"}`}>{p.discount_pct}%</span>}
      <span className={`font-bold tabular-nums ${big ? "text-2xl" : "text-base"}`}>{won(p.sale_price)}</span>
      {p.discount_pct > 0 && <span className="text-xs text-ink-500 line-through tabular-nums">{won(p.list_price)}</span>}
    </div>
  );
}

export function ProductCard({ p }: { p: Product }) {
  return (
    <Link href={`/products/${p.id}`} className="group flex w-full flex-col gap-2">
      <div className="relative rounded-md bg-surface-2 p-4 transition group-hover:bg-ink-100">
        <Cover title={p.title} hue={p.hue} category={p.category} className="mx-auto max-w-[170px]" />
        {p.sold_out && <span className="absolute left-2 top-2 chip bg-ink-900 text-surface">일시품절</span>}
        {!p.returnable && <span className="absolute right-2 top-2 chip bg-amber-100 text-amber-800">환불 제한</span>}
      </div>
      <div className="min-w-0">
        <h3 className="line-clamp-2 break-keep text-sm font-semibold leading-snug group-hover:underline">{p.title}</h3>
        <p className="mt-0.5 truncate text-xs text-ink-500">{p.author} · {p.publisher}</p>
        <div className="mt-1.5"><Price p={p} /></div>
        <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-500">
          <Stars value={p.rating} size={12} />
          <span>{p.rating ?? "–"}</span>
          <span>({p.review_count})</span>
        </div>
      </div>
    </Link>
  );
}

export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label={title}
        className={`max-h-[92vh] w-full overflow-y-auto rounded-2xl bg-surface p-5 shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"}`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn-quiet px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function PageTitle({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-sm text-ink-500">{sub}</p>}
      </div>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-ink-300 px-4 py-12 text-center text-sm text-ink-500">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-800">{error}</p>;
}

export function RequireLogin({ children, user, ready }: { children: ReactNode; user: unknown; ready: boolean }) {
  if (!ready) return <div className="py-20 text-center text-sm text-ink-500">불러오는 중…</div>;
  if (!user) {
    return (
      <Empty>
        로그인이 필요한 화면입니다. <Link href="/login" className="font-semibold text-brand-600 underline">데모 로그인</Link>
      </Empty>
    );
  }
  return <>{children}</>;
}
