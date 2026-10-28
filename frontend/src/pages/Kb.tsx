import { useEffect, useMemo, useState } from "react";
import { IconSearch } from "@/components/icons";
import { Loading, OFFLINE } from "@/components/admin/bits";
import { api } from "@/lib/api";

type Chunk = Awaited<ReturnType<typeof api.admin.kb>>[number];

export default function KnowledgeBasePage() {
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.admin.kb().then(setChunks).catch(() => setError(OFFLINE)); }, []);

  const docs = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const map = new Map<string, Chunk[]>();
    for (const c of (chunks ?? []).filter((c) => !needle || [c.title, c.section, c.text, c.id].some((t) => t.toLowerCase().includes(needle)))) map.set(c.title, [...(map.get(c.title) ?? []), c]);
    return [...map.entries()];
  }, [chunks, q]);

  if (!chunks) return <Loading error={error} />;
  return (
    <div className="space-y-4 px-8 pb-10 pt-2">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">지식베이스</h1>
          <p className="text-sm text-ink-500">답변 초안·검증 가드가 근거로 쓰고, 클레임 판정이 규칙 번호(예: KB-REF-01#1)로 인용하는 정책 문서 {chunks.length}개 섹션</p>
        </div>
        <label className="relative ml-auto w-full max-w-xs">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-500"><IconSearch size={16} /></span>
          <input className="field rounded-full pl-9" placeholder="정책 검색 (예: 환불, KB-DEL-01)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="정책 검색" />
        </label>
      </div>
      {docs.map(([title, items]) => (
        <section key={title} className="card p-5">
          <h2 className="card-title mb-3">{title}</h2>
          <ul className="divide-y divide-ink-100">
            {items.map((c) => (
              <li key={c.id} className="py-3">
                <div className="flex items-center gap-2"><span className="font-mono text-[11px] text-ink-500">{c.id}</span><span className="text-sm font-medium">{c.section}</span></div>
                <p className="mt-1 text-sm leading-relaxed text-ink-700">{c.text}</p>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {!docs.length && <p className="text-sm text-ink-500">검색 결과가 없습니다.</p>}
    </div>
  );
}
