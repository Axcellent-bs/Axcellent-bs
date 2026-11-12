import { useCallback, useEffect, useState } from "react";
import { DeadlineSection } from "@/components/finance/CloseControls";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ActionQueue } from "@/components/finance/SafetyQueue";
import { SettlementsSection, type SettlementSource } from "@/components/finance/Settlements";
import { CompanyName } from "@/demo/CompanyName";
import { api } from "@/lib/api";
import { SETTLEMENT_HOLD_ACTION, liveHumanTokenSet } from "@/lib/finance";

// 고객지원 > 판매자 정산. 판매자 정산(정산금 계산 · 지급 승인 요청 · 묶음 상태 · 지급 기한 점검)은
// 고객지원(claims 서비스)이 한다. 재무는 결과를 받아 재무 화면 ③에서 보여 주기만 한다.
// 표와 기한 점검은 dashboard-api(`/api/dashboard/settlements...`)에서 받는다. 숫자는 서버가 계산한 값 그대로다.
// 묶음 요청은 안전 장치 보류 큐로 가고(항상 사람), 아래 보류 큐에서 사람이 진행·멈춤을 누른다.

const REFRESH_MS = 30_000;

const SOURCE: SettlementSource = {
  settlements: (period) => api.admin.settlements(period),
  deadlines: () => api.admin.settlementDeadlines(),
};
const loadDeadlines = () => api.admin.settlementDeadlines();
const request = async (period: string) => (await api.admin.requestSettlementBatch(period)).data;

const DEADLINE_HINT = "구매가 확정된 판매 대금을 정해진 날 안에 판매자에게 줄 수 있는지 미리 봅니다. 기한이 가까운 판매자는 보류 사유를 풀거나 이번 지급 묶음에 넣으세요. 지급은 정산 묶음 요청 → 안전 장치 보류 큐 승인으로만 합니다.";
const QUEUE_INTRO = "판매자 정산 묶음 지급 승인은 항상 사람이 정합니다. 위 정산 표의 판매자·기간·금액과 같은지 보고 결정하세요.";

export default function SellerSettlementsPage() {
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const t = setInterval(bump, REFRESH_MS);
    return () => clearInterval(t);
  }, [bump]);

  // 묶음을 요청하면 보류 큐를 다시 받고 그 건을 표시한다
  const [highlight, setHighlight] = useState<string | null>(null);
  const goToQueue = useCallback((reviewId: string | null) => {
    setHighlight(reviewId);
    bump();
    requestAnimationFrame(() => document.getElementById("safety-queue")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }, [bump]);

  return (
    <div className="w-full space-y-6 px-4 pb-12 pt-2 sm:space-y-8 sm:px-8">
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">판매자 정산</h1>
          <p className="mt-1 text-sm text-ink-500">
            <CompanyName />의 고객지원팀이 판매자에게 줄 돈을 주마다 계산하고, 보류 없는 판매자만 묶어 지급 승인을 요청합니다.
            요청은 안전 장치 보류 큐로 가고, 사람이 승인해야 지급이 기록됩니다. 재무는 이 결과를 받아 장부에 씁니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm sm:ml-auto">
          <button className="btn-ghost" onClick={bump}>새로고침</button>
        </div>
      </header>

      <ErrorBoundary title="판매자 정산 화면을 그리지 못했습니다">
        <SettlementsSection tick={tick} source={SOURCE} request={request} canRequest={liveHumanTokenSet}
          onChanged={bump} onGoToQueue={goToQueue} title="판매자 정산 내역" titleHidden={false} />
      </ErrorBoundary>

      <DeadlineSection tick={tick} load={loadDeadlines} hint={DEADLINE_HINT} />

      <ActionQueue action={SETTLEMENT_HOLD_ACTION} intro={QUEUE_INTRO} highlight={highlight} reloadKey={tick} />
    </div>
  );
}
