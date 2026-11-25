import { useEffect, useState } from "react";
import { Link } from "react-router";
import { SheetBoard } from "@/components/marketing/SheetBoard";
import { StoryboardView } from "@/components/marketing/StoryboardMaker";
import { RenderPanel } from "@/components/marketing/RenderPanel";
import { ScriptPanel } from "@/components/marketing/ScriptPanel";
import { assetUrl, getCreativeRecord, redrawBoardImage, thumbSrc, type CreativeRecord, type RenderPartKey } from "@/lib/creativeRecords";
import { recordLink, renderPending, renderTab, type TabChip } from "@/lib/creativeCardView";
import { useAuthedMedia } from "@/lib/authedMedia";
import type { CreativeCard } from "@/lib/planningData";

// 마케팅 1:1 대화(DirectChat)의 이미지 시트·스토리보드 카드.
// - 사람이 올린 말(role=attached): 올린 캐릭터의 작은 미리보기와 기록 번호
// - 마케팅 답(role=result): 이미지 시트 배치판 + (있으면) 대본(접기, 읽기만) + 스토리보드 칸 + 제작 기록 링크
//   대본을 고치려면 제작 기록 상세에서 한다(11/23)
// 기록은 GET /marketing/creative-records/{id}로 읽는다. 같은 기록·같은 상태는 한 번만 읽는다.

const POLL_MS = 5_000;
const cache = new Map<string, Promise<CreativeRecord>>();
const cacheKey = (card: CreativeCard) => `${card.record_id}:${card.has_storyboard ? 1 : 0}:${card.has_script ? 1 : 0}`;
const load = (card: CreativeCard) => {
  const key = cacheKey(card);
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
  // 그림이 만드는 중이면 5초마다 다시 읽어 끝나면 그림으로 바꾼다 (읽은 값은 캐시에도 넣는다)
  const pending = !!rec && renderPending(rec.render?.sheet_image, rec.render?.board_image);
  useEffect(() => {
    if (!pending) return;
    let live = true;
    const timer = window.setInterval(() => {
      getCreativeRecord(card.record_id).then((r) => {
        if (!live) return;
        cache.set(cacheKey(card), Promise.resolve(r));
        setRec(r);
      }, () => { /* 다음 차례에 다시 읽는다 */ });
    }, POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [pending, card]);

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
  const sheetTab = renderTab(rec.render?.sheet_image);
  const boardTab = renderTab(rec.render?.board_image);
  return (
    <div className="mt-2 w-full max-w-3xl space-y-3">
      <details open={!rec.storyboard && !rec.script}>
        <summary className="cursor-pointer text-xs font-medium text-ink-700">이미지 시트{sheetTab.chip && <TabChipView chip={sheetTab.chip} />}</summary>
        <div className="pt-2">
          {sheetTab.show === "image"
            ? <RenderImage recordId={card.record_id} part="sheet_image" version={sheetTab.version} label="이미지 시트 그림" />
            : <SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} />}
        </div>
      </details>
      {rec.script && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-ink-700">대본 · {rec.script.scenes.length}장면 {rec.script.total_seconds}초</summary>
          <div className="pt-2"><ScriptPanel recordId={card.record_id} script={rec.script} editable={false} /></div>
        </details>
      )}
      {(rec.storyboard || boardTab.show === "image") && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-ink-700">스토리보드{boardTab.chip && <TabChipView chip={boardTab.chip} />}</p>
          {boardTab.show === "image"
            ? <RenderImage recordId={card.record_id} part="board_image" version={boardTab.version} label="스토리보드 그림" />
            : rec.storyboard && <StoryboardView r={rec.storyboard} onRedraw={async () => { await redrawBoardImage(card.record_id); }} />}
        </div>
      )}
      <RenderPanel recordId={card.record_id} />
      <Link to={recordLink(card.record_id)} className="inline-block text-xs text-brand-700 underline">제작 기록에서 크게 보기 · {card.record_id}</Link>
    </div>
  );
}

const CHIP_TONE: Record<TabChip["tone"], string> = {
  ok: "bg-emerald-50 text-emerald-800", run: "bg-brand-50 text-brand-700", fail: "bg-amber-50 text-amber-800",
};

function TabChipView({ chip }: { chip: TabChip }) {
  return <span className={`chip ml-2 py-0 text-[11px] ${CHIP_TONE[chip.tone]}`}>{chip.label}</span>;
}

/** 제작 기록에 저장된 Higgsfield 그림 (제작 기록 화면과 같은 주소·사람 토큰 읽기) */
function RenderImage({ recordId, part, version, label }: { recordId: string; part: RenderPartKey; version: string | null; label: string }) {
  const { src, failed } = useAuthedMedia(assetUrl(recordId, part, version));
  return src
    ? <img src={src} alt={`${label} (Higgsfield, AI 생성)`} className="w-full rounded-xl ring-1 ring-ink-100" />
    : <div className="flex h-32 items-center justify-center rounded-xl bg-ink-50 text-xs text-ink-500">{failed ? "그림을 불러오지 못했어요" : "그림을 불러오는 중…"}</div>;
}
