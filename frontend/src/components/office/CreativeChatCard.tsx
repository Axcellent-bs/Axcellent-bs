import { useEffect, useState } from "react";
import { Link } from "react-router";
import { SheetBoard } from "@/components/marketing/SheetBoard";
import { StoryboardView } from "@/components/marketing/StoryboardMaker";
import { RenderPanel } from "@/components/marketing/RenderPanel";
import { getCreativeRecord, thumbSrc, type CreativeRecord } from "@/lib/creativeRecords";
import type { CreativeCard } from "@/lib/planningData";

// 마케팅 1:1 대화(DirectChat)의 이미지 시트·스토리보드 카드.
// - 사람이 올린 말(role=attached): 올린 캐릭터의 작은 미리보기와 기록 번호
// - 마케팅 답(role=result): 이미지 시트 배치판 + (있으면) 스토리보드 칸 + 제작 기록 링크
// 기록은 GET /marketing/creative-records/{id}로 읽는다. 같은 기록·같은 상태는 한 번만 읽는다.

const cache = new Map<string, Promise<CreativeRecord>>();
const load = (card: CreativeCard) => {
  const key = `${card.record_id}:${card.has_storyboard ? 1 : 0}`;
  let p = cache.get(key);
  if (!p) {
    p = getCreativeRecord(card.record_id);
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return p;
};

export function CreativeChatCard({ card }: { card: CreativeCard }) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    load(card).then((r) => live && setRec(r), (e: unknown) => live && setError(e instanceof Error ? e.message : "기록을 읽지 못했어요"));
    return () => { live = false; };
  }, [card]);

  if (error) return <p className="mt-1 text-xs text-accent">{error}</p>;
  if (card.role === "attached") {
    const src = thumbSrc(rec?.thumbnail_base64);
    return (
      <div className="mt-1 flex items-center gap-2 rounded-xl bg-white px-2 py-1.5 text-xs text-ink-700 ring-1 ring-ink-100">
        {src ? <img src={src} alt="올린 캐릭터 이미지" className="h-12 w-12 rounded-lg object-contain" /> : <span className="h-12 w-12 animate-pulse rounded-lg bg-ink-100" aria-hidden="true" />}
        <span>캐릭터 이미지 · <span className="tabular-nums">{card.record_id}</span></span>
      </div>
    );
  }
  if (!rec) return <p className="mt-1 animate-pulse text-xs text-ink-500">이미지 시트를 불러오는 중…</p>;
  return (
    <div className="mt-2 w-full max-w-3xl space-y-3">
      <details open={!rec.storyboard}>
        <summary className="cursor-pointer text-xs font-medium text-ink-700">이미지 시트</summary>
        <div className="pt-2"><SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} /></div>
      </details>
      {rec.storyboard && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-ink-700">스토리보드</p>
          <StoryboardView r={rec.storyboard} />
        </div>
      )}
      <RenderPanel recordId={card.record_id} />
      <Link to={`/marketing/records?id=${card.record_id}`} className="inline-block text-xs text-brand-700 underline">제작 기록에서 크게 보기 · {card.record_id}</Link>
    </div>
  );
}
