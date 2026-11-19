import { useCallback, useEffect, useState } from "react";
import { DASHBOARD_API_URL, HUMAN_TOKEN_SET } from "@/lib/env";
import { briefs } from "@/lib/briefs";
import type { PublishLine, PublishState } from "@/lib/briefMedia";
import {
  MANUAL_LIMIT_NOTE,
  MANUAL_STEPS,
  canRecordManual,
  manualCaptionText,
  manualPackageErrorKo,
  manualRecordErrorKo,
  permalinkProblemKo,
  type ManualPackage,
} from "@/lib/manualPublish";

// 수동 게시 안내 카드. 자동 게시가 꺼진 동안 승인된 건(status=manual_pending)을 사람이 Instagram 앱에서
// 직접 올리고, 게시물 링크를 입력해야만 "게시 완료"로 기록된다.
// - 캡션·해시태그·AI 표시 문구·미디어 정보는 서버가 승인된 값으로 준 것만 보여 준다(여기서 고치지 않는다).
// - 링크는 필수이고 형식이 맞아야 버튼이 켜진다. 서버가 같은 검사를 다시 한다(중복 링크도 거절).
// - 링크가 실제로 있는 게시물인지는 시스템이 확인하지 못한다. 그 한계를 화면에 적는다.
// - 기록한 사람 이름은 서버가 사람 토큰에서 채운다.

export function ManualPublishCard({ state, onDone }: { state: PublishState; onDone: () => void }) {
  const draftId = state.post_draft_id;
  const [pkg, setPkg] = useState<ManualPackage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [labelChecked, setLabelChecked] = useState(false);
  const [link, setLink] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!draftId) return;
    try {
      const answer = await briefs.getManualPublishPackage(draftId);
      setPkg(answer.output && answer.output.caption !== undefined ? answer.output : null);
      setLoadError(answer.output ? null : manualPackageErrorKo(undefined));
    } catch (e) {
      setPkg(null);
      setLoadError(manualPackageErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code));
    }
  }, [draftId]);
  useEffect(() => { void load(); }, [load]);

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(manualCaptionText(pkg));
      setCopied("ok");
    } catch {
      setCopied("fail"); // 복사가 막힌 환경: 아래 글을 직접 선택해 복사한다
    }
  }

  async function record() {
    if (!draftId || !canRecordManual({ permalink: link, labelChecked, tokenSet: HUMAN_TOKEN_SET, busy })) return; // 클릭 잠금
    setBusy(true);
    setError(null);
    try {
      await briefs.completeManualPublish(draftId, link.trim(), labelChecked);
      onDone(); // 상태를 다시 읽으면 "직접 게시됨"으로 바뀐다
    } catch (e) {
      setError(manualRecordErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code));
    } finally {
      setBusy(false);
    }
  }

  const problem = permalinkProblemKo(link);
  const previewUrl = state.preview_url ? `${DASHBOARD_API_URL}${state.preview_url}` : null;
  const isVideo = state.preview_kind === "video";

  return (
    <section className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/50 px-3 py-3" aria-label="수동 게시 안내">
      <div>
        <p className="font-semibold text-ink-900">수동 게시 안내</p>
        <p className="mt-0.5 text-ink-700">
          자동 게시가 꺼져 있어 사람이 직접 올려야 합니다. 올린 뒤 게시물 링크를 기록하면 게시 완료로 닫힙니다.
          {pkg?.pause_reason ? ` 꺼 둔 사유: ${pkg.pause_reason}` : ""}
        </p>
      </div>
      <ol className="list-decimal space-y-0.5 pl-5 text-ink-700">
        {MANUAL_STEPS.map((step) => <li key={step}>{step}</li>)}
      </ol>

      {!draftId && <p className="text-red-700" role="alert">서버가 게시 건 번호를 주지 않아 이 화면에서 기록할 수 없습니다.</p>}
      {loadError && <p className="text-red-700" role="alert">{loadError}</p>}

      {pkg && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-primary text-xs" onClick={() => void copyCaption()}>캡션 복사</button>
            {copied === "ok" && <span className="text-brand-700" role="status">복사했습니다.</span>}
            {copied === "fail" && <span className="text-amber-800" role="status">복사하지 못했습니다. 아래 글을 직접 선택해 복사해 주세요.</span>}
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-white px-2 py-1 text-ink-900" aria-label="게시할 캡션 전문">{pkg.caption}</pre>
          <p className="text-ink-500">
            해시태그 {pkg.hashtags.length}개가 캡션에 들어 있습니다. AI 표시 문구 "{pkg.ai_label_text}"{pkg.ai_label_in_caption ? "가 캡션에 들어 있습니다." : "는 캡션에 없습니다. 담당자에게 확인해 주세요."}
          </p>
        </div>
      )}

      {previewUrl && (
        <div className="space-y-1">
          {isVideo
            ? <video className="max-h-60 rounded-xl bg-black" src={previewUrl} controls preload="metadata" aria-label="올릴 영상" />
            : <img className="max-h-60 rounded-xl" src={previewUrl} alt="올릴 이미지" />}
          <p className="flex flex-wrap gap-3">
            <a className="font-medium text-brand-700 underline" href={previewUrl} target="_blank" rel="noopener noreferrer">미디어 크게 보기</a>
            <a className="font-medium text-brand-700 underline" href={previewUrl} download={pkg?.media[0]?.file_name ?? "media"}>미디어 내려받기</a>
          </p>
        </div>
      )}

      <label className="flex items-start gap-2 text-ink-700">
        <input type="checkbox" className="mt-0.5" checked={labelChecked} onChange={(e) => setLabelChecked(e.target.checked)} />
        <span>Instagram 앱에서 AI 생성 표시(AI 정보 라벨)를 켜고 올렸습니다.</span>
      </label>

      <div className="space-y-1">
        <label className="block text-ink-700" htmlFor={`manual-link-${draftId ?? "x"}`}>게시물 링크(필수)</label>
        <input
          id={`manual-link-${draftId ?? "x"}`}
          className="field w-full"
          value={link}
          inputMode="url"
          autoComplete="off"
          placeholder="https://www.instagram.com/p/…/"
          aria-invalid={touched && problem !== null}
          onChange={(e) => setLink(e.target.value)}
          onBlur={() => setTouched(true)}
        />
        {touched && problem && <p className="text-red-700" role="alert">{problem}</p>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary text-xs" disabled={!canRecordManual({ permalink: link, labelChecked, tokenSet: HUMAN_TOKEN_SET, busy })} onClick={() => void record()}>
          {busy ? "기록하는 중" : "게시 완료로 기록"}
        </button>
        {!HUMAN_TOKEN_SET && <span className="text-ink-500">사람 토큰이 설정되지 않아 기록할 수 없습니다.</span>}
      </div>
      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-red-800" role="alert">{error}</p>}
      <p className="text-ink-500">{MANUAL_LIMIT_NOTE}</p>
    </section>
  );
}

/** 게시 상태 한 줄. 게시된 건은 Instagram 링크를 새 탭 링크로 건다(형식이 맞는 값만) */
export function PublishLineView({ line, tone }: { line: PublishLine; tone: Record<string, string> }) {
  return (
    <p className={`rounded-xl px-3 py-2 ${tone[line.tone]}`} role="status">
      {line.text}
      {line.link && <> <a className="font-medium underline" href={line.link} target="_blank" rel="noopener noreferrer">게시물 보기</a></>}
    </p>
  );
}
