import { useMemo } from "react";
import { speaker } from "@/lib/agentChat";
import { ago } from "@/lib/format";
import { handoffRequests, requestTitle, shortNo } from "@/lib/requestHandoffs";
import { REQUEST_STATUS } from "@/lib/workRequests";
import { useWorkRequests } from "@/components/office/useWorkRequests";
import { HiddenRequests, RequestActions } from "@/components/office/RequestActions";
import { STALE_HOURS, staleHours } from "@/lib/officeRequests";

// 고객 의뢰가 부서 사이로 넘어가는 모습: AI 대화방·AI 오피스 공용 카드. 장애 이슈와 따로, 의뢰 전용으로 그린다.
// 평소에는 접어 두고(의뢰가 늘어도 아래 대화 칸이 밀리지 않게) 펼치면 한 줄에 카드 3개씩 보인다.
// 의뢰 목록(GET /api/requests)은 사람 토큰이 있어야 읽힌다. 토큰이 없으면(직원 로그인 전) 아무것도 그리지 않고 조회도 하지 않는다.
// 고객 글·부서가 돌려준 문장은 옮기지 않는다. 일 이름·단계 이름·부서·상태만 보인다.
// 11/23: 의뢰마다 ⋯ 메뉴 [의뢰문 수정] [의뢰 삭제] (RequestActions, 원문은 수정 칸에만), 목록 아래 "숨긴 의뢰 N건 보기"

const SHOWN = 12;

/** compact: 접힌 한 줄(작은 글자·얇은 줄)로 둔다. 펼치면 지금 카드 그대로 (AI 오피스 머리·AI 대화방 방 목록 위) */
export function RequestHandoffCard({ compact = false }: { compact?: boolean } = {}) {
  // 의뢰 목록은 같은 페이지의 AI 오피스 자리 불·대화방 진행 카드와 한 번만 읽어 나눠 쓴다 (10초마다, 사람 토큰 없으면 읽지 않음)
  const { hasToken, rows, tasks, failed } = useWorkRequests();

  const all = useMemo(() => handoffRequests(rows ?? []), [rows]);
  const groups = all.slice(0, SHOWN);
  const now = Date.now();
  if (!hasToken) return null;

  return (
    <details className={compact ? "card shrink-0 px-3 py-1.5" : "card px-5 py-4"} aria-label="고객 의뢰 넘김">
      <summary className={`cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${compact ? "text-[13px]" : "text-[15px]"}`}>
        고객 의뢰 넘김
        {rows !== null && <span className={`ml-2 font-normal tabular-nums text-ink-500 ${compact ? "text-xs" : "text-[13px]"}`}>{all.length}건</span>}
      </summary>
      {rows === null ? (
        <p className="mt-2 text-[13px] text-ink-500">{failed ? "의뢰 기록을 불러오지 못했어요" : "불러오는 중…"}</p>
      ) : groups.length === 0 ? (
        <p className="mt-2 text-[13px] text-ink-500">아직 부서로 넘어간 의뢰가 없어요</p>
      ) : (
        <>
          <ul className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {groups.map(({ row, handoffs }) => (
              <li key={row.request_id} className="rounded-xl border border-ink-100 p-3 text-[13px]">
                <p className="flex items-start gap-2">
                  <b className="text-[14px] font-semibold text-ink-900">{requestTitle(row, tasks)}</b>
                  <span className={`chip shrink-0 ${REQUEST_STATUS[row.status].tone}`}>{REQUEST_STATUS[row.status].label}</span>
                  {staleHours(row, now) !== null && <span className="chip shrink-0 bg-ink-100 text-ink-500" title={`${STALE_HOURS}시간 넘게 바뀌지 않아 AI 오피스 자리 상태에는 쓰지 않아요`}>오래 멈춤 · {staleHours(row, now)}시간</span>}
                </p>
                <p className="mt-0.5 text-[12px] tabular-nums text-ink-500" title={row.request_id}>
                  의뢰 {shortNo(row.request_id)} · {ago(row.created_at)}
                </p>
                <RequestActions row={row} title={requestTitle(row, tasks)} />
                <ol className="mt-2 space-y-2">
                  {handoffs.map((h, i) => (
                    <li key={h.id} className={h.planned ? "text-ink-500" : "text-ink-900"}>
                      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                        <span className="tabular-nums text-ink-500">{i + 1}.</span>
                        <b className="font-medium">{speaker(h.from)}</b>
                        <span aria-label="에게">→</span>
                        <b className="font-medium">{speaker(h.to)}</b>
                        <span className={`chip ${h.tone}`}>{h.statusLabel}</span>
                      </p>
                      <p className="pl-4">{h.label}</p>
                    </li>
                  ))}
                </ol>
              </li>
            ))}
          </ul>
          {all.length > SHOWN && <p className="mt-2 text-[13px] text-ink-500">최근 {SHOWN}건만 보여요</p>}
        </>
      )}
      {rows !== null && <HiddenRequests titleOf={(r) => requestTitle(r, tasks)} />}
    </details>
  );
}
