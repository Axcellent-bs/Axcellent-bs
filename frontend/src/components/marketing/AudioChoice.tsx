import { useState } from "react";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { briefs } from "@/lib/briefs";
import { audioDecisionKo, audioHttpErrorKo, audioIntroKo, audioResultKo, bgmUsageKo, canChooseAudio, type AudioChoice as Choice, type AudioResult, type MediaEstimate, type PreparedMedia } from "@/lib/briefMedia";

// 만든 영상의 소리를 사람이 한 번 정하는 확인 단계: "이 영상 그대로 사용" / "배경음악 따로 만들기".
// - 영상은 AI 공급자가 만든 소리를 그대로 두고 한 번만 만든다. 별도 배경음악은 여기서 고를 때만 만든다.
// - 선택은 한 번뿐이고 서버가 기록한다(감사). 게시는 하지 않는다: 게시는 C 등급 사람 승인이다.
// - 긴 영상 경고창(시작 전)과 다른 단계라 겹치지 않는다: 이 패널은 영상이 만들어진 뒤에만 보인다.
// - 숫자·상태는 서버가 준 값만 쓴다.

/** 소리를 정하는 중인 기획안. 같은 건을 두 번 보내지 않는다 */
const choosing = new Set<string>();

export function AudioChoice({ proposalId, prepared, estimate, publishStatus, onDone }: { proposalId: string; prepared: PreparedMedia; estimate: MediaEstimate | null | undefined; publishStatus: string | null | undefined; onDone: () => void }) {
  const audio = prepared.audio;
  const [step, setStep] = useState<"idle" | "confirm" | "busy">(() => (choosing.has(proposalId) ? "busy" : "idle"));
  const [choice, setChoice] = useState<Choice>("keep");
  const [listened, setListened] = useState(false);
  const [result, setResult] = useState<AudioResult | null>(null);
  if (!audio || audio.mode === null) return null;

  async function send(c: Choice) {
    if (choosing.has(proposalId)) return; // 클릭 잠금
    choosing.add(proposalId);
    setStep("busy");
    setResult(null);
    try {
      setResult(audioResultKo(await briefs.chooseAudio(proposalId, c, listened), c));
    } catch (e) {
      setResult({ kind: "failed", text: audioHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      choosing.delete(proposalId);
      setStep("idle");
      onDone();
    }
  }

  if (audio.decision) {
    return (
      <div className="space-y-1" aria-label={`${proposalId} 영상 소리`}>
        <p className="text-ink-700">{audioDecisionKo(audio.decision)}</p>
        {result && <p className={result.kind === "failed" ? "text-red-800" : "text-brand-700"} role="status">{result.text}</p>}
      </div>
    );
  }
  if (audio.mode !== "source") return null; // 예전 설정(무음 등)으로 만든 영상은 고르는 단계가 없다
  const canChoose = canChooseAudio(prepared, publishStatus);
  const needListen = audio.has_source_audio;
  const keepBlocked = needListen && !listened;

  return (
    <div className="space-y-2 rounded-xl border border-ink-100 bg-white px-3 py-3" role="group" aria-label={`${proposalId} 영상 소리 정하기`}>
      <p className="font-medium text-ink-900">영상의 소리를 정해 주세요</p>
      {audioIntroKo(audio).map((line) => <p key={line} className="text-ink-700">{line}</p>)}
      {!canChoose ? (
        <p className="text-ink-500">게시 요청이 이미 올라가 있어 소리를 바꿀 수 없습니다.</p>
      ) : step === "busy" ? (
        <p className="text-ink-700" role="status">처리하는 중입니다. 배경음악은 만드는 데 잠시 걸립니다. 이 화면을 닫지 말고 기다려 주세요.</p>
      ) : step === "confirm" ? (
        <div className="space-y-2" role="group" aria-label="소리 선택 확인">
          <p className="font-medium text-ink-700">{choice === "keep" ? "이 영상 그대로 쓸까요?" : "배경음악을 따로 만들까요?"}</p>
          <ul className="list-disc space-y-0.5 pl-5 text-ink-700">
            {choice === "keep" ? (
              <>
                <li>영상에 들어 있는 소리를 그대로 씁니다. 새로 만드는 것은 없고 사용량도 늘지 않습니다.</li>
                <li>AI 표시는 그대로 들어갑니다. 게시는 사람 승인이 한 번 더 필요합니다.</li>
              </>
            ) : (
              <>
                <li>{bgmUsageKo(estimate?.bgm)}</li>
                <li>영상 소리를 AI가 만든 배경음악(노래·가사·목소리 없는 연주곡)으로 바꿉니다. 영상 화면은 그대로입니다.</li>
                <li>이달 사용량 예산을 넘거나 안전 장치가 판단하면 사람 확인으로 보류될 수 있습니다. 보류되면 영상은 그대로 두고 비용도 나지 않습니다.</li>
                <li>AI 표시를 다시 입히고 검사를 다시 합니다.</li>
              </>
            )}
            <li>선택은 한 번뿐이고 나중에 바꿀 수 없습니다. 기록으로 남습니다.</li>
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" onClick={() => void send(choice)}>{choice === "keep" ? "그대로 사용하기" : "배경음악 만들기"}</button>
            <button className="btn-ghost text-xs" onClick={() => setStep("idle")}>취소</button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {needListen && (
            <label className="flex items-center gap-2 text-ink-700">
              <input type="checkbox" checked={listened} onChange={(e) => setListened(e.target.checked)} />
              <span>위 영상의 소리를 끝까지 들어 봤습니다</span>
            </label>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn-primary text-xs"
              disabled={!HUMAN_TOKEN_SET || keepBlocked}
              title={!HUMAN_TOKEN_SET ? "사람 토큰이 없어 쓸 수 없습니다" : keepBlocked ? "소리를 들어 보고 체크해 주세요" : undefined}
              onClick={() => { setChoice("keep"); setResult(null); setStep("confirm"); }}
            >
              이 영상 그대로 사용
            </button>
            <button
              className="btn-ghost text-xs"
              disabled={!HUMAN_TOKEN_SET}
              title={!HUMAN_TOKEN_SET ? "사람 토큰이 없어 쓸 수 없습니다" : undefined}
              onClick={() => { setChoice("bgm"); setResult(null); setStep("confirm"); }}
            >
              배경음악 따로 만들기
            </button>
          </div>
          <p className="text-ink-500">{bgmUsageKo(estimate?.bgm)}</p>
        </div>
      )}
      {result && step !== "busy" && (
        <p className={`rounded-xl px-3 py-2 ${result.kind === "failed" || result.kind === "check_rejected" ? "bg-red-50 text-red-800" : result.kind === "held" || result.kind === "working" ? "bg-amber-50 text-amber-800" : "bg-brand-50 text-brand-700"}`} role={result.kind === "failed" ? "alert" : "status"}>{result.text}</p>
      )}
    </div>
  );
}
