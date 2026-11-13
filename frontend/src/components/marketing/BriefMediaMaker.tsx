import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { shortDateTime, won } from "@/lib/format";
import { briefs, mediaKindKo, mediaPreviewUrl, type StoredBrief } from "@/lib/briefs";
import { ApprovalPanel } from "./ApprovalPanel";
import { approvalLink, estimateBlockKo, estimateLines, isReel, mediaHttpErrorKo, mediaResultKo, publishHttpErrorKo, publishReasonKo, publishResultKo, publishStatusLine, waitKo, PUBLISH_EXPIRY_MIN, type MediaResult, type PreparedMedia, type PublishResult, type PublishState } from "@/lib/briefMedia";

// 넘어온 기획안 한 건의 "미디어 만들기". 저장된 미디어 요청(종류·필드)을 서버가 그대로 실행해 초안을 만든다.
// - 누르면 먼저 예상 비용(서버가 단가표·장부에서 계산한 값)을 보여 주고, 사람이 확인해야 시작한다.
// - 이 화면은 게시하지 않는다. "게시 요청"은 C 등급 승인 대기 건(10분 만료)만 만들고, 게시는 사람 승인(카드 안 승인 패널 또는 승인 화면) 뒤에만 된다.
// - 승인 시간이 지나 닫힌 건만 사람이 "다시 요청"할 수 있다(자동 재요청 없음).
// - 숫자·상태는 서버가 준 값만 쓴다. 서버가 주지 않으면 비워 두고 버튼을 끈다.

/** 만드는 중인 기획안. 목록이 새로 그려져도(15초 새로고침) 같은 건을 두 번 시작하지 않는다 */
const inFlight = new Set<string>();

/** 게시 요청을 보내는 중인 기획안. 같은 건을 두 번 요청하지 않는다 */
const publishing = new Set<string>();

const TONE: Record<string, string> = { info: "bg-brand-50 text-brand-700", ok: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-red-800" };

function PublishRequest({ proposalId, prepared, state, onDone }: { proposalId: string; prepared: PreparedMedia; state: PublishState | null | undefined; onDone: () => void }) {
  const [step, setStep] = useState<"idle" | "confirm" | "busy">(() => (publishing.has(proposalId) ? "busy" : "idle"));
  const [result, setResult] = useState<PublishResult | null>(null);
  const retryMode = useRef(false); // 확인 단계가 "다시 요청"인지
  const line = publishStatusLine(state);
  const link = (
    <Link className="font-medium text-brand-700 underline" to={approvalLink(state?.review_id)}>사람 승인 화면 열기</Link>
  );

  async function send(retry = false) {
    if (publishing.has(proposalId)) return; // 클릭 잠금
    publishing.add(proposalId);
    setStep("busy");
    setResult(null);
    try {
      setResult(publishResultKo(await briefs.requestPublish(proposalId, retry)));
    } catch (e) {
      setResult({ kind: "failed", text: publishHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      publishing.delete(proposalId);
      setStep("idle");
      onDone();
    }
  }

  // 서버가 요청할 수 있다고 알려 준 때만 버튼을 둔다(이미 대기·게시·거절된 건, 캐러셀 등은 숨김)
  const canRequest = HUMAN_TOKEN_SET && state?.can_request === true && state.status === "not_requested";
  // 승인 시간이 지나 닫힌 건만 사람이 다시 요청할 수 있다(서버가 can_retry로 알려 준다)
  const canRetry = HUMAN_TOKEN_SET && state?.status === "expired" && state.can_retry === true;
  const retryBlocked = (state?.status === "expired" || state?.status === "rejected") && state.can_retry !== true && state.retry_reason_code
    ? publishReasonKo(state.retry_reason_code) : null;
  return (
    <div className="space-y-2" aria-label={`${proposalId} 게시 요청`}>
      {line && <p className={`rounded-xl px-3 py-2 ${TONE[line.tone]}`} role="status">{line.text}</p>}
      {state?.status === "held" && <ApprovalPanel key={state.review_id ?? "held"} state={state} onDone={onDone} />}
      {state?.status === "held" && <p>{link}</p>}
      {retryBlocked && <p className="text-ink-500">{retryBlocked}</p>}
      {step === "busy" ? (
        <p className="text-ink-700" role="status">게시 요청을 보내는 중입니다.</p>
      ) : step === "confirm" ? (
        <div className="space-y-2" role="group" aria-label="게시 요청 확인">
          <p className="font-medium text-ink-700">게시를 요청할까요?</p>
          <ul className="list-disc space-y-0.5 pl-5 text-ink-700">
            <li>{mediaKindKo(isReel(prepared.media_kind) ? "video" : "image")}을(를) Instagram에 올리는 요청입니다. 요청만으로는 게시되지 않습니다.</li>
            <li>게시될 글(수정할 수 없습니다):
              <span className="mt-1 block max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-white px-2 py-1 text-ink-900">{state?.caption ?? "서버가 글을 알려 주지 않았습니다."}</span>
            </li>
            <li>AI 생성물 표시가 파일과 게시 값에 들어갑니다{prepared.label_text ? ` ("${prepared.label_text}")` : ""}.</li>
            <li>사람 승인이 한 번 더 필요합니다. 승인 화면에서 진행을 눌러야 게시됩니다.</li>
            <li>승인 요청은 {PUBLISH_EXPIRY_MIN}분 뒤 만료됩니다. 시간이 지나 닫히면 사람이 다시 요청할 수 있습니다. 사람이 거절한 건은 다시 요청할 수 없습니다.</li>
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" onClick={() => void send(retryMode.current)}>{retryMode.current ? "다시 요청 보내기" : "게시 요청 보내기"}</button>
            <button className="btn-ghost text-xs" onClick={() => setStep("idle")}>취소</button>
          </div>
        </div>
      ) : canRetry ? (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" onClick={() => { retryMode.current = true; setResult(null); setStep("confirm"); }}>다시 요청</button>
          <span className="text-ink-500">승인 시간이 지나 닫힌 건을 새 승인 대기로 다시 올립니다. 게시는 사람 승인 뒤에만 됩니다.</span>
        </div>
      ) : canRequest ? (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary text-xs" onClick={() => { retryMode.current = false; setResult(null); setStep("confirm"); }}>게시 요청</button>
          <span className="text-ink-500">누르면 확인 단계가 먼저 나옵니다. 게시는 사람 승인 뒤에만 됩니다.</span>
        </div>
      ) : null}
      {result && step !== "busy" && (
        <p className={`rounded-xl px-3 py-2 ${result.kind === "failed" ? "bg-red-50 text-red-800" : "bg-brand-50 text-brand-700"}`} role={result.kind === "failed" ? "alert" : "status"}>
          {result.text} {result.kind !== "failed" && link}
        </p>
      )}
    </div>
  );
}

function Preview({ proposalId, prepared }: { proposalId: string; prepared: PreparedMedia }) {
  const [broken, setBroken] = useState(false);
  const url = mediaPreviewUrl(proposalId, prepared.asset_sha256);
  if (broken) return <p className="text-ink-500">미리보기 파일을 불러올 수 없습니다.</p>;
  return isReel(prepared.media_kind)
    ? <video className="max-h-80 rounded-xl bg-black" src={url} controls preload="metadata" onError={() => setBroken(true)} aria-label="AI 표시가 들어간 영상 미리보기" />
    : <img className="max-h-80 rounded-xl" src={url} alt="AI 표시가 들어간 이미지 미리보기" onError={() => setBroken(true)} />;
}

function Prepared({ proposalId, prepared, publish, onDone }: { proposalId: string; prepared: PreparedMedia; publish: PublishState | null | undefined; onDone: () => void }) {
  const ready = prepared.stage === "draft_ready";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {ready
          ? <span className="chip bg-brand-50 text-brand-700">{publish && publish.status !== "not_requested" ? "초안 준비됨" : "초안 준비됨 · 게시 요청 전"}</span>
          : <span className="chip bg-amber-50 text-amber-800">초안 검사에 걸림 · 게시할 수 없음</span>}
        <span className="text-ink-500" title={prepared.post_draft_id ?? undefined}>
          {mediaKindKo(isReel(prepared.media_kind) ? "video" : "image")}
          {prepared.prepared_at && ` · ${shortDateTime(prepared.prepared_at)} 만듦`}
          {prepared.cost_krw != null && ` · 생성 비용 ${won(prepared.cost_krw)}`}
        </span>
      </div>
      <Preview proposalId={proposalId} prepared={prepared} />
      <p className="text-ink-500">
        {prepared.label_text ? <>파일에 AI 표시 문구 "{prepared.label_text}"가 들어 있습니다. </> : "AI 표시를 입힌 결과 파일입니다. "}
        {!ready && prepared.check.problems.length > 0 && <>검사에 걸린 항목 {prepared.check.problems.length}개. </>}
      </p>
      {ready && <PublishRequest proposalId={proposalId} prepared={prepared} state={publish} onDone={onDone} />}
      {ready && !publish && (
        <p className="text-ink-700">
          게시는 C 등급이라 사람 승인이 필요합니다. 이 서버는 이 화면에서의 게시 요청을 아직 지원하지 않습니다.{" "}
          <Link className="font-medium text-brand-700 underline" to={approvalLink(null)}>사람 승인 화면 열기</Link>
        </p>
      )}
    </div>
  );
}

export function BriefMediaMaker({ item, onDone }: { item: StoredBrief; onDone: () => void }) {
  const id = item.proposal_id;
  const h = item.sns_handoff;
  const request = h?.status === "handed" ? h.media_request : null;
  const estimate = h?.media_estimate ?? null;
  const [step, setStep] = useState<"idle" | "confirm" | "busy">(() => (inFlight.has(id) ? "busy" : "idle"));
  const [result, setResult] = useState<MediaResult | null>(null);
  const [fresh, setFresh] = useState<{ key: string; prepared: PreparedMedia } | null>(null);
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (step !== "busy") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [step]);

  if (!request) return null;
  // 목록(서버)이 알려 준 상태가 우선이고, 방금 만든 결과는 목록이 새로 올 때까지만 쓴다
  // (요청 초안이 그 뒤 바뀌었으면 방금 결과는 옛 미디어라 보여 주지 않는다)
  const requestKey = JSON.stringify([request.media_kind, request.media_ref, request.fields]);
  const prepared = h?.prepared ?? (fresh?.key === requestKey ? fresh.prepared : null);
  const ready = prepared?.stage === "draft_ready";
  const kind = request.media_kind;

  async function make() {
    if (inFlight.has(id)) return; // 클릭 잠금: 같은 건을 두 번 보내지 않는다
    inFlight.add(id);
    setStep("busy");
    setSeconds(0);
    setResult(null);
    try {
      const answer = await briefs.makeMedia(id);
      setResult(mediaResultKo(answer));
      const made = answer.output?.sns_handoff?.prepared;
      setFresh(made ? { key: requestKey, prepared: made } : null);
    } catch (e) {
      setResult({ kind: "failed", text: mediaHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      inFlight.delete(id);
      setStep("idle");
      onDone();
    }
  }

  const blocked = !estimate ? "서버가 예상 비용을 알려 주지 않아 만들 수 없습니다. 서버가 이 기능을 지원하는지 확인해 주세요."
    : estimate.status !== "ok" ? estimateBlockKo(estimate.reason_code) : null;

  return (
    <div className="w-full space-y-2 rounded-lg bg-ink-50 px-3 py-3 text-xs" aria-label={`${id} 미디어 만들기`}>
      {prepared && <Prepared proposalId={id} prepared={prepared} publish={h?.publish} onDone={onDone} />}

      {step === "busy" ? (
        <p className="text-ink-700" role="status">만드는 중입니다 ({seconds}초 지남). {waitKo(kind)}</p>
      ) : step === "confirm" && estimate ? (
        <div className="space-y-2" role="group" aria-label="비용 확인">
          <p className="font-medium text-ink-700">{mediaKindKo(kind)}을(를) 만들까요?</p>
          <ul className="list-disc space-y-0.5 pl-5 text-ink-700">
            {estimateLines(estimate).map((line) => <li key={line}>{line}</li>)}
            <li>{waitKo(kind)}</li>
            <li>만든 파일에는 AI 표시가 들어가고, 만든 뒤에도 게시는 사람 승인이 필요합니다.</li>
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" onClick={() => void make()}>만들기 시작</button>
            <button className="btn-ghost text-xs" onClick={() => setStep("idle")}>취소</button>
          </div>
        </div>
      ) : !ready && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="btn-primary text-xs"
            disabled={!HUMAN_TOKEN_SET || blocked !== null}
            title={!HUMAN_TOKEN_SET ? "사람 토큰이 없어 쓸 수 없습니다" : undefined}
            onClick={() => { setResult(null); setStep("confirm"); }}
          >
            {prepared ? "미디어 다시 만들기" : "미디어 만들기"}
          </button>
          {blocked ? <span className="text-amber-800">{blocked}</span>
            : <span className="text-ink-500">누르면 예상 비용을 먼저 보여 줍니다. 게시는 하지 않습니다.</span>}
        </div>
      )}

      {result && step !== "busy" && result.kind !== "ready" && (
        <p className={`rounded-xl px-3 py-2 ${result.kind === "failed" || result.kind === "check_rejected" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`} role="alert">{result.text}</p>
      )}
      {result?.kind === "ready" && step !== "busy" && <p className="text-brand-700" role="status">{result.text}</p>}
    </div>
  );
}
