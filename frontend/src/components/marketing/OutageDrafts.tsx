import { useCallback, useState, type ReactNode } from "react";
import { Skeleton } from "@/components/finance/bits";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { shortDateTime } from "@/lib/format";
import { ApiError } from "@/lib/apiError";
import { mergeHttpErrorKo, mergeSummaryKo, previewDraftIds, skipReasonKo, type MergeAnswer } from "@/lib/outageMerge";
import { briefs, briefsEnabled, briefsError, outageTriggerKo, type OutageSnsDraft } from "@/lib/briefs";
import { OutageProceed } from "./OutageProceed";

// 장애 사과·보상 글 초안 카드. 마케팅 장애·공지 탭에 놓는다(전에는 SNS·미디어 탭에 있었다).
// 초안 목록 읽기, 유사 초안 병합(미리보기 → 확인 → 저장), "이 초안으로 진행"(OutageProceed)을 그대로 쓴다.

const REFRESH_MS = 15_000;

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="py-6 text-center text-sm text-ink-500">{children}</p>;

export function OutageDrafts() {
  const [outage, setOutage] = useState<OutageSnsDraft[] | null>(null);
  const [outageError, setOutageError] = useState<string | null>(null);
  const [mergedTotal, setMergedTotal] = useState(0);
  const [showMerged, setShowMerged] = useState(false);
  // 유사 초안 병합: 미리보기(저장 없음) → 사람이 확인 → 저장. busy는 중복 클릭 잠금
  const [mergePreview, setMergePreview] = useState<MergeAnswer | null>(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [mergeDone, setMergeDone] = useState<string | null>(null);

  const load = useCallback(async () => {
if (briefsEnabled) {
  try {
    const listed = await briefs.outageSnsDrafts(20, showMerged);
    setOutage(listed.items);
    setMergedTotal(listed.merged_total ?? 0);
    setOutageError(null);
  } catch (e) {
    const err = e as { status?: number; message?: string; code?: string | null };
    setOutageError(briefsError({ status: err.status, message: err.message ?? "알 수 없는 오류", code: err.code }));
  }
}
  }, [showMerged]);

  useVisibleInterval(load, REFRESH_MS);

  const runMerge = async (confirm: boolean) => {
    if (mergeBusy) return;
    setMergeBusy(true);
    setMergeError(null);
    try {
      if (!confirm) {
        setMergeDone(null);
        setMergePreview(await briefs.mergeOutageSnsDrafts(true));
      } else if (mergePreview) {
        const done = await briefs.mergeOutageSnsDrafts(false, previewDraftIds(mergePreview));
        setMergePreview(null);
        setMergeDone(mergeSummaryKo(done));
        await load();
      }
    } catch (e) {
      const err = e as ApiError;
      setMergeError(mergeHttpErrorKo(err.status, err.code));
    } finally {
      setMergeBusy(false);
    }
  };

  return (
    <Section title="장애 사과·보상 글 초안" desc="장애 때 사과 공지가 나가거나 보상 쿠폰을 드리면 마케팅 AI가 SNS 글 초안을 바로 만들어 둡니다. 초안일 뿐 게시된 글이 아닙니다. 카드의 '이 초안으로 진행'을 누르면 확인 뒤 이미지를 만들어 붙이고 게시 요청(승인 대기)까지 보내며, 게시는 사람이 승인 패널에서 한 번 더 승인해야 합니다(C 등급).">
      <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
        <li>원인은 확인된 경우에만 쉬운 말로 적고, 확인되지 않았으면 "원인 확인 중"이라고 적습니다.</li>
        <li>내부 용어·숫자·담당자 이름은 넣지 않고, AI 생성 표시와 시연용 가상 서비스 안내가 들어갑니다.</li>
      </ul>
      {briefsEnabled && (
        <div className="space-y-3 rounded-2xl border border-ink-100 p-4" aria-label="유사 초안 병합">
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn-ghost text-xs" onClick={() => void runMerge(false)} disabled={mergeBusy || mergePreview !== null}>{mergeBusy && !mergePreview ? "확인하는 중" : "유사 초안 병합"}</button>
            <p className="text-xs text-ink-500">거의 같은 초안을 대표 1건만 남기고 접습니다. 새 글을 만들지 않고, 접힌 초안은 지우지 않으며, 게시와 비용은 없습니다.</p>
            {(mergedTotal > 0 || showMerged) && (
              <label className="ml-auto flex items-center gap-2 text-xs text-ink-700">
                <input type="checkbox" checked={showMerged} onChange={(e) => setShowMerged(e.target.checked)} />
                병합됨 {mergedTotal}건 보기
              </label>
            )}
          </div>
          {mergeError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{mergeError}</p>}
          {mergeDone && <p role="status" className="rounded-xl bg-brand-50 px-4 py-2 text-sm text-brand-700">{mergeDone}</p>}
          {mergePreview && (
            <div className="space-y-3">
              <p className="text-sm font-medium text-ink-700">미리보기: {mergeSummaryKo(mergePreview)} 아직 저장하지 않았습니다.</p>
              {mergePreview.groups.map((g) => (
                <div key={g.representative} className="space-y-1 rounded-xl bg-ink-50 p-3 text-xs">
                  <p><span className="chip bg-brand-50 text-brand-700">남는 대표</span> <span className="tabular-nums text-ink-500">{g.representative}</span> · 접히는 초안 {g.merged_from.length}건 <span className="tabular-nums text-ink-500">({g.merged_from.join(", ")})</span></p>
                  <p className="line-clamp-3 whitespace-pre-wrap text-ink-700">{g.representative_caption}</p>
                </div>
              ))}
              {mergePreview.skipped.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500" aria-label="병합하지 않은 초안">
                  {mergePreview.skipped.map((k) => <li key={k.id}><span className="tabular-nums">{k.id}</span>: {skipReasonKo(k.reason)}</li>)}
                </ul>
              )}
              <div className="flex gap-2">
                {mergePreview.groups.length > 0 && <button className="btn-primary text-xs" onClick={() => void runMerge(true)} disabled={mergeBusy}>{mergeBusy ? "병합하는 중" : `${mergePreview.merged_count}건 병합하기`}</button>}
                <button className="btn-ghost text-xs" onClick={() => setMergePreview(null)} disabled={mergeBusy}>{mergePreview.groups.length > 0 ? "취소" : "닫기"}</button>
              </div>
            </div>
          )}
        </div>
      )}
      {!briefsEnabled ? <Empty>마케팅 읽기 연결이 꺼져 있어 초안 목록을 읽을 수 없습니다.</Empty>
        : outageError ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{outageError}</p>
          : outage === null ? <Skeleton stats={0} lines={2} />
            : outage.length === 0 ? <Empty>만들어진 초안이 없습니다.</Empty> : (
              <ul className="space-y-3" aria-label="장애 사과·보상 글 초안 목록">
                {outage.map((d) => (
                  <li key={d.post_draft_id} className="space-y-2 rounded-2xl border border-ink-100 p-4">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="chip bg-brand-50 text-brand-700">{outageTriggerKo(d.trigger)}</span>
                      <span className="chip bg-amber-50 text-amber-800">초안 · 게시 안 됨</span>
                      {d.status === "merged" && <span className="chip bg-ink-50 text-ink-700" title={d.merged_into ?? undefined}>병합됨 · 대표 초안 아래로 접힘</span>}
                      {d.merged_from && d.merged_from.length > 0 && <span className="chip bg-ink-50 text-ink-700">비슷한 초안 {d.merged_from.length}건 병합</span>}
                      <span className="text-ink-500">{d.media_flow?.prepared ? "이미지가 붙은 초안" : d.media_attached ? "미디어가 붙은 초안" : "캡션 초안만 있음 (미디어 없음)"}</span>
                      <span className="ml-auto tabular-nums text-ink-500" title={`초안 ${d.post_draft_id} · 이벤트 ${d.context_id}`}>{shortDateTime(d.created_at)} 만듦</span>
                    </div>
                    <p className="whitespace-pre-wrap text-sm text-ink-700">{d.caption}</p>
                    {d.status !== "merged" && <OutageProceed draft={d} onDone={() => void load()} />}
                  </li>
                ))}
              </ul>
            )}
    </Section>
  );
}
