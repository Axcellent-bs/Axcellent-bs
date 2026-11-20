import { useState } from "react";

// 마케팅 › SNS·미디어 › 이미지 시트·스토리보드 결과에서 같이 쓰는 복사 버튼과 프롬프트 상자.

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "failed">("idle");
  const copy = () => navigator.clipboard.writeText(text).then(() => setState("ok"), () => setState("failed"));
  return (
    <button type="button" className="btn-ghost text-xs" onClick={() => void copy()} aria-label={`${label} 복사`}>
      {state === "ok" ? "복사했어요" : state === "failed" ? "복사 실패" : "복사"}
    </button>
  );
}

export function PromptBox({ title, text }: { title: string; text: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
        <span className="ml-auto"><CopyButton text={text} label={title} /></span>
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface p-3 text-xs leading-relaxed text-ink-700 ring-1 ring-ink-100">{text}</pre>
    </div>
  );
}
