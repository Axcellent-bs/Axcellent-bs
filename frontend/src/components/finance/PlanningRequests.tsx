import { useState } from "react";
import { actorText } from "@/lib/actor";
import { finance, humanTokenSet } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { Empty, Panel, ResourceBody, useResource } from "./bits";

// 재무 → 기획 개선 요청: 사람이 정한 요청만 보낸다. 기획이 근거를 되물으면 재무 AI가 보낸 근거로 답한다.
// 재무는 요청만 보내고 기획서·우선순위는 기획이 정한다.
export function PlanningRequestsSection({ tick }: { tick: number }) {
  const res = useResource("planning-requests", () => finance.planningRequests(), tick);
  const [title, setTitle] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const ready = humanTokenSet && !busy && title.trim().length > 0 && evidence.trim().length > 0;

  async function send() {
    setBusy(true);
    setMessage(null);
    try {
      const got = (await finance.sendPlanningRequest(title.trim(), evidence.trim())).data;
      setMessage(got.outcome === "linked_duplicate"
        ? `기획에 같은 요청(${got.duplicate_of})이 있어 그 요청에 이었습니다.`
        : `기획 요청 ${got.request_id}로 보냈습니다.`);
      setTitle("");
      setEvidence("");
      await res.reload();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel id="planning-requests" title="기획에 개선 요청"
      hint="재무 업무에서 고칠 점을 기획에 보냅니다. 근거는 한 줄에 하나씩 적어 주세요. 기획이 근거를 되물으면 재무 AI가 이 근거로 답합니다.">
      <div className="space-y-2">
        <input className="field w-full" placeholder="바꿀 내용 한 줄 (예: PG 정산 파일을 매일 자동으로 받기)" maxLength={120}
          value={title} onChange={(e) => setTitle(e.target.value)} disabled={!humanTokenSet || busy} />
        <textarea className="field w-full" rows={3} maxLength={2000} placeholder="근거 (예: 지난주 대사 예외 4건이 하루 늦게 잡힘)"
          value={evidence} onChange={(e) => setEvidence(e.target.value)} disabled={!humanTokenSet || busy} />
        <div className="flex items-center gap-3">
          <button type="button" className="btn-primary" disabled={!ready} onClick={() => void send()}>
            {busy ? "보내는 중…" : "기획에 보내기"}
          </button>
          {message && <span className="text-sm text-ink-600">{message}</span>}
        </div>
      </div>
      <div className="mt-4">
        <ResourceBody res={res}>
          {(d) => d.requests.length === 0
            ? <Empty>아직 보낸 요청이 없습니다.</Empty>
            : (
              <ul className="divide-y divide-ink-100 text-sm">
                {d.requests.map((r) => (
                  <li key={r.source_ref} className="py-2">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <b className="text-ink-900">{r.title}</b>
                      <code className="font-mono text-ink-500">{r.request_id}</code>
                    </div>
                    <div className="text-ink-500">{actorText(r.by_label, r.by)} · {shortDateTime(r.at)} · 근거 {r.facts.length}줄</div>
                  </li>
                ))}
              </ul>
            )}
        </ResourceBody>
      </div>
    </Panel>
  );
}
