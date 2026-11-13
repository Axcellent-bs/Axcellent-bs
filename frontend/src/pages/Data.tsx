import { useCallback, useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { ActivityReport } from "@/components/data/ActivityReport";
import { IncidentData } from "@/components/data/IncidentData";
import { LearningKpi } from "@/components/data/LearningKpi";
import { TrendScan } from "@/components/data/TrendScan";
import { Chip, TONE, useResource } from "@/components/finance/bits";
import { DATA_SERVER, ORCH_SERVER, dataAi, sortRecent } from "@/lib/data";
import { orchestrator } from "@/lib/orchestrator";
import { useCompany } from "@/demo/company";

// 데이터분석 AI 화면 = 고객사 데이터분석팀. 짜임은 재무 AI(/finance)와 같다.
// 왼쪽 사이드바의 하위 탭 3개가 화면을 가른다(demo/services.ts data 서비스 children):
// - /data 장애 영향 분석: 장애 사건 데이터(이상 감지 → 영향 분석 → 복구 확인 → 처리 시간 요약, ?incident=로 사건 고르기) + 그 아래 회사 현황 리포트(activity_report의 회사 숫자·달라진 점)
// - /data/ops 운영 지표: 복구 속도 · AI 단독 처리율 · 평상시 지표 (components/data/LearningKpi.tsx)
// - /data/trend 트렌드&콘텐츠: 감지 → 추천 → 넘김 → 성과 (components/data/TrendScan.tsx, 하위 탭은 #detect 등)
// 옛 주소(/data#daily · #report · #trend · #trend/<단계>)는 새 주소로 옮긴다. 숫자는 서버가 계산한 값만 보여 주고, 없으면 빈 상태를 보여 준다(예시 숫자를 만들지 않는다).
// 실서버 연결 방법과 화면이 부르는 주소: frontend/README.md "데이터 분석 화면 확인 방법"

const REFRESH_MS = 30_000;

type Mode = "incident" | "ops" | "trend";
const MODE_PATH: Record<Mode, string> = { incident: "/data", ops: "/data/ops", trend: "/data/trend" };

/** 옛 주소(/data#daily 등)가 가야 할 새 주소. 새 주소면 null */
function legacyTarget(frag: string): { pathname: string; hash: string } | null {
  if (frag === "#daily") return { pathname: MODE_PATH.ops, hash: "" };
  if (frag === "#report") return { pathname: MODE_PATH.incident, hash: "#report" };
  if (frag === "#trend") return { pathname: MODE_PATH.trend, hash: "" };
  if (frag.startsWith("#trend/")) return { pathname: MODE_PATH.trend, hash: `#${frag.slice("#trend/".length)}` };
  return null;
}

export default function DataPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const legacy = legacyTarget(location.hash);
  const mode: Mode = location.pathname.startsWith(MODE_PATH.trend) ? "trend" : location.pathname.startsWith(MODE_PATH.ops) ? "ops" : "incident";
  const urlIncident = new URLSearchParams(location.search).get("incident");

  /** 사건 고르기: 주소(?incident=)에 남겨 새로고침해도 같은 사건을 본다 */
  const openIncident = useCallback((id: string) => {
    navigate({ pathname: MODE_PATH.incident, search: `?incident=${encodeURIComponent(id)}`, hash: "" }, { replace: true });
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

  // 옛 주소는 새 주소로 옮긴다 (?incident= 같은 쿼리는 그대로)
  if (legacy) return <Navigate replace to={{ pathname: legacy.pathname, search: location.search, hash: legacy.hash }} />;

  return (
    <div className={`w-full px-4 pb-12 pt-2 sm:px-8 ${mode === "trend" ? "space-y-4" : "space-y-6 sm:space-y-8"}`}>
      {/* 트렌드&콘텐츠는 첫 화면에 탭과 내용이 보이게 제목 한 줄만 (TrendScan). 설명은 그 안 접힘 */}
      {mode !== "trend" && <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">데이터 분석</h1>
          <p className="mt-1 text-sm text-ink-500">
            {company ? `${company.name} 데이터분석팀입니다.` : "데이터분석팀입니다."} 장애가 나면 실패한 주문·영향 고객·결제 실패(미결제) 금액을 세고, 복구됐는지 지표로 확인하고, 처리 시간을 요약합니다.
            운영 지표에서는 복구 속도와 AI 단독 처리율을, 트렌드&콘텐츠에서는 감지 → 추천 → 넘김 → 성과를 봅니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:ml-auto">
          <button className="btn-ghost" onClick={bump}>새로고침</button>
          {orchUp != null && <Chip label={orchUp ? "총괄 켜짐" : "총괄 꺼짐"} tone={orchUp ? TONE.ok : TONE.bad} />}
          {dataUp != null && <Chip label={dataUp ? "데이터 AI 켜짐" : "데이터 AI 꺼짐"} tone={dataUp ? TONE.ok : TONE.bad} />}
        </div>
      </header>}

      {orchUp === false && (
        <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          {ORCH_SERVER}에 연결할 수 없어요. 장애 사건 데이터를 받을 수 없습니다.
        </p>
      )}
      {dataUp === false && (
        <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          {DATA_SERVER}에 연결할 수 없어요. 운영 지표·리포트·트렌드를 받을 수 없습니다.{orchUp ? " 장애 사건 데이터는 총괄 기록으로 계속 보입니다." : ""}
        </p>
      )}

      {mode === "incident" && (
        <div className="space-y-6 sm:space-y-8">
          <IncidentData list={list} detail={detail} selected={selected} onSelect={openIncident} />
          <ActivityReport tick={tick} />
        </div>
      )}
      {mode === "ops" && <LearningKpi tick={tick} incidents={list.env?.data ?? null} />}
      {mode === "trend" && <TrendScan dataUp={dataUp} company={company?.name ?? null} />}
    </div>
  );
}
