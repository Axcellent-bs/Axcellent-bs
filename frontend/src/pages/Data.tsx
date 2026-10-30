import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { ActivityReport } from "@/components/data/ActivityReport";
import { IncidentData } from "@/components/data/IncidentData";
import { LearningKpi } from "@/components/data/LearningKpi";
import { Chip, TONE, useResource } from "@/components/finance/bits";
import { DATA_SERVER, ORCH_SERVER, dataAi, sortRecent } from "@/lib/data";
import { orchestrator } from "@/lib/orchestrator";
import { useCompany } from "@/demo/company";

// 데이터분석 AI 화면 = 고객사 데이터분석팀. 짜임은 재무 AI(/finance)와 같다.
// 기본 화면은 결제 장애의 "장애 사건 데이터": 이상 감지 → 영향 분석 → 복구 확인 → 처리 시간 요약.
// 두 번째 탭 "평상시 지표"는 학습 효과·KPI. 세 번째 탭 "리포트"(#report)는 부서별 AI 업무 리포트(activity_report). 숫자는 서버가 계산한 값만 보여 주고, 없으면 빈 상태를 보여 준다(예시 숫자를 만들지 않는다).
// 실서버 연결 방법과 화면이 부르는 주소: frontend/README.md "데이터 분석 화면 확인 방법"

const REFRESH_MS = 30_000;

type Mode = "incident" | "daily" | "report";
const MODE_LABEL: Record<Mode, string> = { incident: "장애 사건 데이터", daily: "평상시 지표", report: "리포트" };
const MODE_HASH: Record<Mode, string> = { incident: "", daily: "#daily", report: "#report" };

export default function DataPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const mode: Mode = location.hash === "#daily" ? "daily" : location.hash === "#report" ? "report" : "incident";
  const urlIncident = new URLSearchParams(location.search).get("incident");

  const openMode = useCallback((m: Mode) => {
    navigate({ search: location.search, hash: MODE_HASH[m] }, { replace: true });
  }, [navigate, location.search]);
  /** 사건 고르기: 주소(?incident=)에 남겨 새로고침해도 같은 사건을 본다 */
  const openIncident = useCallback((id: string) => {
    navigate({ search: `?incident=${encodeURIComponent(id)}`, hash: "" }, { replace: true });
  }, [navigate]);

  // 30초마다 모두 새로 받는다
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const t = setInterval(bump, REFRESH_MS);
    return () => clearInterval(t);
  }, [bump]);

  const company = useCompany();

  // 서버 상태: 총괄(사건 데이터)과 데이터 AI(평상시 지표)를 따로 본다
  const [orchUp, setOrchUp] = useState<boolean | null>(null);
  const [dataUp, setDataUp] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    orchestrator.health().then(() => live && setOrchUp(true)).catch(() => live && setOrchUp(false));
    dataAi.health().then(() => live && setDataUp(true)).catch(() => live && setDataUp(false));
    return () => { live = false; };
  }, [tick]);

  const list = useResource("incidents", async () => {
    const r = await orchestrator.incidents();
    return { data: sortRecent(r.data), meta: { mock: false } };
  }, tick);
  const selected = urlIncident ?? list.env?.data[0]?.context_id ?? null;
  const detail = useResource(`incident-${selected ?? ""}`, async () => {
    if (!selected) throw new Error("고른 사건이 없습니다");
    return { data: await orchestrator.incident(selected), meta: { mock: false } };
  }, tick);

  return (
    <div className="w-full space-y-6 px-4 pb-12 pt-2 sm:space-y-8 sm:px-8">
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">데이터 분석</h1>
          <p className="mt-1 text-sm text-ink-500">
            {company ? `${company.name} 데이터분석팀입니다.` : "데이터분석팀입니다."} 장애가 나면 실패한 주문·영향 고객·피해 금액을 세고, 복구됐는지 지표로 확인하고, 처리 시간을 요약합니다.
            평소에는 같은 장애가 다시 났을 때 얼마나 빨리 복구했는지, AI가 사람 없이 끝낸 비율을 봅니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:ml-auto">
          <button className="btn-ghost" onClick={bump}>새로고침</button>
          {orchUp != null && <Chip label={orchUp ? "총괄 켜짐" : "총괄 꺼짐"} tone={orchUp ? TONE.ok : TONE.bad} />}
          {dataUp != null && <Chip label={dataUp ? "데이터 AI 켜짐" : "데이터 AI 꺼짐"} tone={dataUp ? TONE.ok : TONE.bad} />}
        </div>
      </header>

      {orchUp === false && (
        <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          {ORCH_SERVER}에 연결할 수 없어요. 장애 사건 데이터를 받을 수 없습니다.
        </p>
      )}
      {dataUp === false && (
        <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          {DATA_SERVER}에 연결할 수 없어요. 평상시 지표와 리포트를 받을 수 없습니다.{orchUp ? " 장애 사건 데이터는 총괄 기록으로 계속 보입니다." : ""}
        </p>
      )}

      {/* 맨 위 전환: 장애 사건 데이터 | 평상시 지표 | 리포트 */}
      <div role="tablist" aria-label="데이터분석 화면" className="flex w-full gap-1 rounded-2xl bg-surface p-1 ring-1 ring-ink-100 sm:w-auto sm:max-w-xl">
        {(["incident", "daily", "report"] as Mode[]).map((m) => (
          <button key={m} type="button" role="tab" id={`data-mode-${m}`} aria-selected={mode === m} aria-controls={`data-mode-panel-${m}`} onClick={() => openMode(m)}
            className={`flex-1 rounded-xl px-4 py-2.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${mode === m ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {MODE_LABEL[m]}
          </button>
        ))}
      </div>

      {mode === "incident" && (
        <div role="tabpanel" id="data-mode-panel-incident" aria-labelledby="data-mode-incident">
          <IncidentData list={list} detail={detail} selected={selected} onSelect={openIncident} />
        </div>
      )}
      {mode === "daily" && (
        <div role="tabpanel" id="data-mode-panel-daily" aria-labelledby="data-mode-daily">
          <LearningKpi tick={tick} />
        </div>
      )}
      {mode === "report" && (
        <div role="tabpanel" id="data-mode-panel-report" aria-labelledby="data-mode-report">
          <ActivityReport tick={tick} />
        </div>
      )}
    </div>
  );
}
