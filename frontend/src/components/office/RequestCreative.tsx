import { useEffect, useState } from "react";
import { Link } from "react-router";
import { SheetBoard } from "@/components/marketing/SheetBoard";
import { StoryboardView } from "@/components/marketing/StoryboardMaker";
import { approvalLink } from "@/lib/briefMedia";
import { getCreativeRecord, thumbSrc, type CreativeRecord } from "@/lib/creativeRecords";
import { creativeButtons, creativeRecordOf, sceneCells, videoApproval } from "@/lib/requestCharacter";
import type { VideoPlanView } from "@/lib/videoPlan";
import type { WorkRequestRow } from "@/lib/workRequests";

// 대화방 의뢰 진행 카드의 [이미지 시트][스토리보드] · 영상 승인 대기 (홍보 영상 의뢰, 11/23 시안 ②).
// - 의뢰의 제작 기록(CR-…) = 영상 준비 단계 결과 번호. 있으면 대화방 안 패널에 마케팅 1:1과 같은 SheetBoard·StoryboardView
// - 제작 기록이 없는 의뢰(캐릭터 없이)는 [스토리보드]만 두고, 기획 제작안 장면 글을 칸으로 보여 준다(그림 없음)
// - "영상 승인 대기"면 [승인 화면 열기]. 승인은 지금 승인 화면에서만 한다(여기서 바로 승인하지 않는다)

type Tab = "sheet" | "storyboard";

const records = new Map<string, Promise<CreativeRecord>>();
function loadRecord(id: string, version: string): Promise<CreativeRecord> {
  const key = `${id}:${version}`;
  let p = records.get(key);
  if (!p) {
    p = getCreativeRecord(id);
    p.catch(() => records.delete(key));
    records.set(key, p);
  }
  return p;
}

function Panel({ recordId, plan, tab, setTab, version }: {
  recordId: string | null; plan: Pick<VideoPlanView, "scenes"> | null; tab: Tab; setTab: (t: Tab) => void; version: string;
}) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!recordId) return;
    let live = true;
    loadRecord(recordId, version).then((r) => live && setRec(r), (e: unknown) => live && setError(e instanceof Error ? e.message : "기록을 읽지 못했어요"));
    return () => { live = false; };
  }, [recordId, version]);

  const tabs: Tab[] = recordId ? ["sheet", "storyboard"] : ["storyboard"];
  return (
    <section aria-label="이미지 시트·스토리보드" className="w-full overflow-hidden rounded-xl border border-ink-100 bg-surface">
      <div role="tablist" className="flex border-b border-ink-100 text-[12.5px]">
        {tabs.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`border-b-2 px-3 py-1.5 ${tab === t ? "border-brand-600 font-semibold text-brand-700" : "border-transparent text-ink-500"}`}>
            {t === "sheet" ? "이미지 시트" : "스토리보드"}
          </button>
        ))}
      </div>
      <div className="max-h-[32rem] overflow-y-auto p-2.5">
        {recordId && error && <p className="text-xs text-red-700">{error}</p>}
        {recordId && !rec && !error && <p className="animate-pulse text-xs text-ink-500">이미지 시트를 불러오는 중…</p>}
        {recordId && rec && tab === "sheet" && <SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} />}
        {recordId && rec && tab === "storyboard" && (rec.storyboard
          ? <StoryboardView r={rec.storyboard} />
          : <p className="text-xs text-ink-500">스토리보드를 아직 붙이지 않았어요</p>)}
        {!recordId && plan && (
          <ol className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {sceneCells(plan).map((c) => (
              <li key={c.key} className="min-w-0 rounded-lg border border-ink-100 bg-ink-50 p-2 text-xs">
                <b className="block text-ink-900">{c.title}</b>
                <span className="block break-words text-ink-700">{c.text}</span>
                {c.caption && <span className="mt-0.5 block break-words text-ink-500">자막 · {c.caption}</span>}
              </li>
            ))}
          </ol>
        )}
        {!recordId && <p className="mt-1.5 text-xs text-ink-500">캐릭터 없이 만드는 의뢰라 기획 제작안의 장면 글만 보여요(그림 없음).</p>}
      </div>
    </section>
  );
}

export function RequestCreative({ row, plan }: { row: WorkRequestRow; plan: Pick<VideoPlanView, "scenes"> | null }) {
  const [open, setOpen] = useState<Tab | null>(null);
  const buttons = creativeButtons(row, plan);
  const approval = videoApproval(row);
  const recordId = creativeRecordOf(row);
  if (!buttons.sheet && !buttons.storyboard && !approval) return null;
  const toggle = (t: Tab) => setOpen((cur) => (cur === t ? null : t));
  return (
    <div className="space-y-2">
      {approval && (
        <p className="flex flex-wrap items-center gap-1.5">
          <span className="chip bg-amber-50 text-[11px] text-amber-800">영상 승인 대기</span>
          <Link className="btn-ghost btn-card inline-flex items-center" to={approvalLink(approval.reviewId)}>승인 화면 열기</Link>
        </p>
      )}
      {(buttons.sheet || buttons.storyboard) && (
        <span className="flex flex-wrap gap-1.5">
          {buttons.sheet && <button type="button" aria-expanded={open === "sheet"} className={`btn-card ${open === "sheet" ? "btn-primary" : "btn-ghost"}`} onClick={() => toggle("sheet")}>이미지 시트</button>}
          {buttons.storyboard && <button type="button" aria-expanded={open === "storyboard"} className={`btn-card ${open === "storyboard" ? "btn-primary" : "btn-ghost"}`} onClick={() => toggle("storyboard")}>스토리보드</button>}
        </span>
      )}
      {open && <Panel recordId={recordId} plan={plan} tab={recordId ? open : "storyboard"} setTab={setOpen} version={row.updated_at} />}
    </div>
  );
}
