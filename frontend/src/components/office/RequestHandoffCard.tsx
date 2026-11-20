import { useEffect, useMemo, useState } from "react";
import { speaker } from "@/lib/agentChat";
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { ago } from "@/lib/format";
import { orchestrator } from "@/lib/orchestrator";
import { handoffRequests } from "@/lib/requestHandoffs";
import { REQUEST_STATUS, type WorkRequestRow } from "@/lib/workRequests";

// 고객 의뢰가 부서 사이로 넘어가는 모습: AI 대화방·AI 오피스 공용 카드. 장애 이슈와 따로, 의뢰 전용으로 그린다.
// 의뢰 목록(GET /api/requests)은 사람 토큰이 있어야 읽힌다. 토큰이 없으면(직원 로그인 전) 아무것도 그리지 않고 조회도 하지 않는다.
// 고객 글·부서가 돌려준 문장은 옮기지 않는다. 의뢰 번호·단계 이름·부서·상태만 보인다.

const POLL_MS = 10_000;
const SHOWN = 3;

export function RequestHandoffCard() {
  const hasToken = SAFETY_HUMAN_TOKEN.length > 0;
  const [rows, setRows] = useState<WorkRequestRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hasToken) return;
    let live = true;
    const load = async () => {
      try {
        const { data } = await orchestrator.workRequests();
        if (live) { setRows(data); setFailed(false); }
      } catch {
        if (live) setFailed(true);
      }
    };
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, [hasToken]);

  const groups = useMemo(() => handoffRequests(rows ?? []).slice(0, SHOWN), [rows]);
  if (!hasToken) return null;

  return (
    <section aria-label="고객 의뢰 넘김" className="card px-4 py-3">
      <h2 className="card-title">고객 의뢰 넘김</h2>
      {rows === null ? (
        <p className="mt-1 text-xs text-ink-500">{failed ? "의뢰 기록을 불러오지 못했어요" : "불러오는 중…"}</p>
      ) : groups.length === 0 ? (
        <p className="mt-1 text-xs text-ink-500">아직 부서로 넘어간 의뢰가 없어요</p>
      ) : (
        <ul className="mt-2 space-y-3">
          {groups.map(({ row, handoffs }) => (
            <li key={row.request_id} className="text-xs">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-ink-500">{row.request_id}</span>
                <span className={`chip ${REQUEST_STATUS[row.status].tone}`}>{REQUEST_STATUS[row.status].label}</span>
                <span className="ml-auto tabular-nums text-ink-500">{ago(row.created_at)}</span>
              </p>
              <ol className="mt-1 space-y-1">
                {handoffs.map((h) => (
                  <li key={h.id} className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${h.planned ? "text-ink-500" : "text-ink-900"}`}>
                    <b className="font-medium">{speaker(h.from)}</b>
                    <span aria-label="에게">→</span>
                    <b className="font-medium">{speaker(h.to)}</b>
                    <span>{h.label}{h.planned ? " (보낼 차례)" : ""}</span>
                    <span className={`chip ${h.tone}`}>{h.statusLabel}</span>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
