import { useEffect, useState } from "react";
import { Link } from "react-router";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { briefs, outageMediaPreviewUrl, type OutageSnsDraft } from "@/lib/briefs";
import { approvalLink, estimateLines, publishStatusLine, PUBLISH_EXPIRY_MIN, type MediaResult, type PublishResult } from "@/lib/briefMedia";
import { OUTAGE_CONFIRM_NOTES, outageBlockKo, outageHttpErrorKo, outageMediaResultKo, outagePublishResultKo, shouldRequestPublish } from "@/lib/outageMedia";
import { ApprovalPanel } from "./ApprovalPanel";

// 장애 사과·보상 글 초안 카드의 "이 초안으로 진행".
// 1) 사람이 한 번 누르면 확인 단계(예상 비용·상한·누적, 게시는 사람 승인이 한 번 더 필요함)가 나온다.
// 2) 확인하면 이미지를 만들어 초안에 붙이고, 곧바로 게시 요청(승인 대기 건, 10분 만료)까지 보낸다. 게시는 하지 않는다.
// 3) 같은 카드에 승인 패널이 뜨고, 사람이 이미지·최종 글을 보고 직접 승인/거절해야 게시된다(두 번째 사람 결정).
// - 첫 클릭은 게시 권한이 아니라 "이미지 생성 진행 확인"이다. 자동 승인·승인 우회·기한 연장은 없다.
// - 숫자·상태는 서버가 준 값(`media_flow`)만 쓴다. 서버가 주지 않으면 버튼을 끈다.

/** 진행 중인 초안. 목록이 새로 그려져도(15초 새로고침) 같은 초안을 두 번 시작하지 않는다 */
const inFlight = new Set<string>();

const TONE: Record<string, string> = { info: "bg-brand-50 text-brand-700", ok: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-red-800" };
type Step = "idle" | "confirm" | "image" | "request";

export function OutageProceed({ draft, onDone }: { draft: OutageSnsDraft; onDone: () => void }) {
  const id = draft.post_draft_id;
  const flow = draft.media_flow ?? null;
  const [step, setStep] = useState<Step>(() => (inFlight.has(id) ? "image" : "idle"));
  const [result, setResult] = useState<MediaResult | null>(null);
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (step !== "image" && step !== "request") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [step]);

  if (draft.status === "merged") return null;
  if (!flow) return <p className="text-xs text-ink-500">서버가 진행 상태를 알려 주지 않아 이 화면에서 진행할 수 없습니다. 서버가 이 기능을 지원하는지 확인해 주세요.</p>;

  const publish = flow.publish;
  const prepared = flow.prepared;
  const line = publishStatusLine(publish);
  const link = <Link className="font-medium text-brand-700 underline" to={approvalLink(publish?.review_id)}>사람 승인 화면 열기</Link>;

  async function sendPublish(retry: boolean): Promise<PublishResult> {
    try {
      return outagePublishResultKo(await briefs.requestOutagePublish(id, retry));
    } catch (e) {
      return { kind: "failed", text: outageHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) };
    }
  }

  // 한 번의 사람 클릭: 이미지 만들기 → (준비되면) 게시 요청. 승인은 하지 않는다
  async function proceed() {
    if (inFlight.has(id)) return; // 클릭 잠금
    inFlight.add(id);
    setStep("image");
    setSeconds(0);
    setResult(null);
    try {
      const answer = await briefs.makeOutageMedia(id);
      const made = outageMediaResultKo(answer);
      if (!shouldRequestPublish(answer)) {
        setResult(made);
        return;
      }
      setStep("request");
      const sent = await sendPublish(false);
      setResult({ kind: sent.kind === "failed" ? "failed" : "ready", text: sent.kind === "failed" ? `이미지는 준비됐지만 게시 요청을 보내지 못했습니다. ${sent.text}` : sent.text });
    } catch (e) {
      setResult({ kind: "failed", text: outageHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      inFlight.delete(id);
      setStep("idle");
      onDone();
    }
  }

  // 이미지는 준비됐지만 게시 요청이 없는 경우(요청 단계 실패 등)나 만료 뒤 사람 재요청
  async function resend(retry: boolean) {
    if (inFlight.has(id)) return;
    inFlight.add(id);
    setStep("request");
    setSeconds(0);
    setResult(null);
    try {
      const sent = await sendPublish(retry);
      setResult({ kind: sent.kind === "failed" ? "failed" : "ready", text: sent.text });
    } finally {
      inFlight.delete(id);
      setStep("idle");
      onDone();
    }
  }

  const canStart = HUMAN_TOKEN_SET && flow.stage === "not_started" && flow.can_proceed && flow.estimate?.status === "ok";
  const blocked = flow.stage === "not_started" && !flow.can_proceed
    ? outageBlockKo(flow.reason_code)
    : flow.stage === "not_started" && !flow.estimate ? "서버가 예상 비용을 알려 주지 않아 진행할 수 없습니다." : null;
  const canSend = HUMAN_TOKEN_SET && flow.stage === "draft_ready" && publish?.status === "not_requested" && publish.can_request === true;
  const canRetry = HUMAN_TOKEN_SET && publish?.status === "expired" && publish.can_retry === true;
  const busy = step === "image" || step === "request";

  return (
    <div className="space-y-2 rounded-lg bg-ink-50 px-3 py-3 text-xs" aria-label={`${id} 이 초안으로 진행`}>
      {prepared && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {flow.stage === "draft_ready"
              ? <span className="chip bg-brand-50 text-brand-700">이미지 준비됨{publish && publish.status !== "not_requested" ? "" : " · 게시 요청 전"}</span>
              : <span className="chip bg-amber-50 text-amber-800">이미지 검사에 걸림 · 게시할 수 없음</span>}
            <span className="text-ink-500">{prepared.label_text ? `AI 표시 "${prepared.label_text}"가 이미지에 들어 있습니다.` : "AI 표시를 입힌 이미지입니다."}</span>
          </div>
          {publish?.status !== "held" && (
            <img className="max-h-60 rounded-xl" src={outageMediaPreviewUrl(id, prepared.asset_sha256)} alt="AI 표시가 들어간 사과 이미지 미리보기" />
          )}
        </div>
      )}

      {line && <p className={`rounded-xl px-3 py-2 ${TONE[line.tone]}`} role="status">{line.text}</p>}
      {publish?.status === "held" && <ApprovalPanel key={publish.review_id ?? "held"} state={publish} onDone={onDone} />}
      {publish?.status === "held" && <p>{link}</p>}

      {busy ? (
        <p className="text-ink-700" role="status">
          {step === "image" ? `이미지를 만들고 있습니다 (${seconds}초 지남). 이 화면을 닫지 말고 기다려 주세요.` : "게시 요청을 보내는 중입니다."}
        </p>
      ) : step === "confirm" && flow.estimate ? (
        <div className="space-y-2" role="group" aria-label="이미지 생성 확인">
          <p className="font-medium text-ink-700">이 초안으로 진행할까요?</p>
          <ul className="list-disc space-y-0.5 pl-5 text-ink-700">
            {estimateLines(flow.estimate).map((l) => <li key={l}>{l}</li>)}
            {OUTAGE_CONFIRM_NOTES.map((n) => <li key={n}>{n}</li>)}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" onClick={() => void proceed()}>이미지 만들기 시작</button>
            <button className="btn-ghost text-xs" onClick={() => setStep("idle")}>취소</button>
          </div>
        </div>
      ) : canStart ? (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" onClick={() => { setResult(null); setStep("confirm"); }}>이 초안으로 진행</button>
          <span className="text-ink-500">누르면 예상 비용을 먼저 보여 줍니다. 게시는 사람이 승인해야 합니다.</span>
        </div>
      ) : canRetry ? (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" onClick={() => void resend(true)}>다시 요청</button>
          <span className="text-ink-500">승인 시간({PUBLISH_EXPIRY_MIN}분)이 지나 닫힌 건을 새 승인 대기로 올립니다. 이미지는 다시 만들지 않습니다. 게시는 사람 승인 뒤에만 됩니다.</span>
        </div>
      ) : canSend ? (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" onClick={() => void resend(false)}>게시 요청 보내기</button>
          <span className="text-ink-500">이미지는 준비됐습니다. 요청만으로는 게시되지 않고, 사람 승인이 필요합니다.</span>
        </div>
      ) : flow.stage === "not_started" ? (
        <p className="text-amber-800">{!HUMAN_TOKEN_SET ? "사람 토큰이 없어 진행할 수 없습니다." : blocked}</p>
      ) : null}

      {result && !busy && (
        <p className={`rounded-xl px-3 py-2 ${result.kind === "failed" || result.kind === "check_rejected" ? "bg-red-50 text-red-800" : result.kind === "ready" ? "bg-brand-50 text-brand-700" : "bg-amber-50 text-amber-800"}`} role={result.kind === "ready" ? "status" : "alert"}>
          {result.text} {result.kind === "ready" && link}
        </p>
      )}
    </div>
  );
}
