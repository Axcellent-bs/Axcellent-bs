import { useState } from "react";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/apiError";
import { dateStr } from "@/lib/format";
import { PURGE_REASON_KO } from "@/lib/labels";
import type { PurgeReport } from "@/lib/types";

// 고객이 개인정보 삭제를 요청한 문의에서, 그 고객의 문의 원문을 지운다 (고객 요청 삭제).
// 안전 관문이 늘 사람에게 보류한다: 처음 누르면 승인 대기(409), 사람이 보류 큐에서 승인한 뒤 같은 버튼을 다시 누르면 집행한다.
// 처리 중인 문의와 클레임이 이어진 문의는 지우지 않고 지우는 날을 알린다. 되돌릴 수 없다.
type Step = "idle" | "previewing" | "preview" | "requesting" | "waiting" | "done";

export function DataPurgeCard({ conversationId }: { conversationId: number }) {
  const [step, setStep] = useState<Step>("idle");
  const [report, setReport] = useState<PurgeReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "처리하지 못했습니다");

  const preview = async () => {
    setStep("previewing"); setError(null);
    try {
      setReport(await api.admin.purgePreview(conversationId));
      setStep("preview");
    } catch (e) { fail(e); setStep("idle"); }
  };

  // 요청과 승인 뒤 집행은 같은 호출이다(서버가 같은 건으로 본다)
  const request = async () => {
    setStep("requesting"); setError(null);
    try {
      const out = await api.admin.purgeCustomerData(conversationId);
      setReport(out);
      setStep(out.applied ? "done" : "preview");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { setStep("waiting"); return; } // 사람 승인 대기
      fail(e); setStep(report ? "preview" : "idle");
    }
  };

  const purgeable = report?.items.filter((i) => i.action === "purge") ?? [];
  const retained = report?.items.filter((i) => i.action === "blocked") ?? [];

  return (
    <div className="rounded-xl border border-ink-100 bg-surface p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <b>개인정보 삭제 요청 처리</b>
        <span className="chip bg-ink-100 text-ink-700">사람 승인 필요</span>
      </div>
      <p className="text-ink-500">고객이 요청한 삭제를 처리합니다. 이 고객의 문의 원문을 <b>[삭제됨]</b>으로 바꾸고 되돌릴 수 없습니다. 안전 보류 큐에서 사람이 승인해야 집행됩니다.</p>

      {step === "idle" && (
        <button type="button" className="btn-ghost mt-2 px-2.5 py-1 text-xs" onClick={() => void preview()}>삭제 범위 미리 보기</button>
      )}
      {step === "previewing" && <p className="mt-2 text-ink-500">확인하는 중…</p>}

      {(step === "preview" || step === "requesting" || step === "waiting" || step === "done") && report && (
        <div className="mt-2 space-y-2">
          <p className="rounded-md bg-ink-50 px-2 py-1.5">
            {step === "done"
              ? <>지운 문의 <b>{report.purge_count}건</b>{retained.length > 0 && <> · 남긴 문의 <b>{retained.length}건</b></>}</>
              : <>지울 문의 <b>{purgeable.length}건</b>{retained.length > 0 && <> · 남길 문의 <b>{retained.length}건</b></>}</>}
          </p>
          {purgeable.length > 0 && (
            <p className="text-[11px] text-ink-700">{step === "done" ? "지운" : "지울"} 문의 번호: {purgeable.map((i) => `#${i.inquiry_id}`).join(", ")}</p>
          )}
          {retained.length > 0 && (
            <ul className="space-y-1">
              {retained.map((i) => (
                <li key={i.inquiry_id} className="rounded-md bg-amber-50 px-2 py-1 text-[11px] text-amber-800">
                  #{i.inquiry_id} 남김: {i.reasons.map((r) => PURGE_REASON_KO[r] ?? r).join(", ")}
                  {i.reasons.some((r) => r === "status_open" || r === "status_escalated")
                    ? <> — 처리를 마친 뒤 다시 요청하면 지울 수 있습니다</>
                    : i.retained_until && <> — {dateStr(i.retained_until)}에 보유기간이 끝나면 지웁니다</>}
                </li>
              ))}
            </ul>
          )}

          {step === "preview" && purgeable.length > 0 && (
            <button type="button" className="btn-primary px-3 py-1 text-xs" onClick={() => void request()}>삭제 요청하기 (사람 승인 필요)</button>
          )}
          {step === "preview" && purgeable.length === 0 && <p className="text-ink-500">지울 문의가 없습니다.</p>}
          {step === "requesting" && <p className="text-ink-500">요청하는 중…</p>}
          {step === "waiting" && (
            <div className="space-y-1.5 rounded-md bg-violet-50 px-2 py-2 text-violet-800" role="status">
              <p><b>승인 대기 중입니다.</b> 안전 보류 큐에서 사람이 승인하면 집행할 수 있습니다. 승인했다면 아래 버튼을 눌러 집행하세요.</p>
              <button type="button" className="btn-primary px-3 py-1 text-xs" onClick={() => void request()}>승인했으니 집행하기</button>
            </div>
          )}
          {step === "done" && <p className="rounded-md bg-emerald-50 px-2 py-1.5 text-emerald-800" role="status">삭제했습니다. 문의 목록을 새로고침하면 지운 문의는 빠집니다.</p>}
        </div>
      )}

      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-relaxed text-red-700">{error}</p>}
    </div>
  );
}
