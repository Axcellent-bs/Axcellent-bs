import { useEffect, useState, type ReactNode } from "react";
import { TrendReportBody } from "@/components/data/TrendReportView";
import { CheckLog, RequestRules } from "@/components/planning/PlanChecks";
import { dataAi, type TrendReport } from "@/lib/data";
import {
  PLAN_MISSING, REPORT_LOADING, expandLabel, inlinePlan, loadFailedKo, missingSvgKo, type InlineLoad,
} from "@/lib/inlineDoc";
import type { VideoPlanView } from "@/lib/videoPlan";

// 대화방 의뢰 카드 안 [▸ 펼쳐 보기] (11/26, 계산은 lib/inlineDoc.ts).
// - 처음엔 접힘. 한 번 연 뒤에는 접어도 내용을 지우지 않고(숨김만), 보고서는 번호마다 한 번만 읽는다
// - 보고서 = 지금 보고서 화면 본문을 읽기 전용으로(기획에 보내기·의뢰 고치기·인쇄 버튼 없음)
// - 기획서 = 카드가 이미 읽은 영상 제작안(새 API 없음). 3D 콘티는 data URI <img>로만

/** 펼치기 버튼 + 한 번 연 뒤 유지되는 칸. 버튼은 부르는 쪽 버튼 줄 안에 둔다(`button`) */
export function useExpand(): { open: boolean; opened: boolean; button: ReactNode } {
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);
  const button = (
    <button type="button" className="btn-ghost btn-card" aria-expanded={open}
      onClick={() => { setOpen((o) => !o); setOpened(true); }}>
      {expandLabel(open)}
    </button>
  );
  return { open, opened, button };
}

const reports = new Map<string, Promise<TrendReport>>();
function loadReport(id: string): Promise<TrendReport> {
  let p = reports.get(id);
  if (!p) {
    p = dataAi.trendReportGet(id).then((r) => r.output);
    p.catch(() => reports.delete(id)); // 실패하면 다음에 다시 읽는다
    reports.set(id, p);
  }
  return p;
}

export function InlineReport({ reportId, open }: { reportId: string; open: boolean }) {
  const [load, setLoad] = useState<InlineLoad<TrendReport>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    loadReport(reportId).then(
      (value) => live && setLoad({ kind: "ok", value }),
      (e: unknown) => live && setLoad({ kind: "error", message: e instanceof Error ? e.message : "" }),
    );
    return () => { live = false; };
  }, [reportId]);
  return (
    <div hidden={!open} className="max-h-[36rem] w-full overflow-y-auto rounded-xl border border-ink-100 bg-surface-2 p-2" aria-label="보고서 펼쳐 보기">
      {load.kind === "loading" && <p className="animate-pulse text-xs text-ink-500">{REPORT_LOADING}</p>}
      {load.kind === "error" && <p role="alert" className="text-xs text-red-700">{loadFailedKo("보고서", load.message)}</p>}
      {load.kind === "ok" && <TrendReportBody report={load.value} readOnly />}
    </div>
  );
}

export function InlinePlan({ plan, open }: { plan: VideoPlanView | null | undefined; open: boolean }) {
  const v = plan ? inlinePlan(plan) : null;
  const missing = v ? missingSvgKo(v.missingSvg, v.scenes.length) : null;
  return (
    <div hidden={!open} className="max-h-[36rem] w-full space-y-2 overflow-y-auto rounded-xl border border-ink-100 bg-surface p-2.5 text-xs" aria-label="기획서 펼쳐 보기">
      {!v && <p className="text-ink-500">{PLAN_MISSING}</p>}
      {v && (
        <>
          <p className="text-[13px] font-semibold text-ink-900">{v.concept}</p>
          <p className="text-ink-500">{v.meta}</p>
          {v.lead && <p className="text-ink-700">주인공 · {v.lead}</p>}
          {v.fallback && <p className="text-amber-800">규칙 초안 · {v.fallback}</p>}
          {missing && <p className="text-amber-800">{missing}</p>}
          {v.svgWhy && <p className="text-amber-800">{v.svgWhy}</p>}
          {plan && <RequestRules v={plan} />}
          {plan && <CheckLog v={plan} />}
          {v.script && <p className="whitespace-pre-line break-words text-ink-700">{v.script}</p>}
          <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {v.scenes.map((s) => (
              <li key={s.key} className="min-w-0 space-y-1 rounded-lg border border-ink-100 bg-ink-50 p-2">
                <b className="block text-ink-900">{s.title}</b>
                {(s.thumb || s.thumbEnd) && (
                  <span className="flex gap-1.5">
                    {s.thumb && <img src={s.thumb} alt={`${s.title} 첫 화면 3D 콘티`} className="w-1/2 rounded-md bg-surface ring-1 ring-ink-100" />}
                    {s.thumbEnd && <img src={s.thumbEnd} alt={`${s.title} 끝 화면 3D 콘티`} className="w-1/2 rounded-md bg-surface ring-1 ring-ink-100" />}
                  </span>
                )}
                {s.frames && <span className="block break-words text-ink-900">{s.frames}</span>}
                <span className="block break-words text-ink-700">{s.visual}</span>
                {s.action && <span className="block break-words text-ink-500">동작 · {s.action}</span>}
                {s.camera && <span className="block break-words text-ink-500">카메라 · {s.camera}</span>}
                {s.caption && <span className="block break-words text-ink-500">자막 · {s.caption}</span>}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
