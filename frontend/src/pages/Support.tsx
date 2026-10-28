import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { AtRisk } from "@/components/support/AtRisk";
import { InquiryCheck } from "@/components/support/InquiryCheck";
import { SurgeChanges } from "@/components/support/SurgeChanges";

// 고객지원 AI 화면 = 캣북스 고객지원팀. 짜임은 데이터분석 AI(/data)·재무 AI(/finance)와 같다.
// 세 탭: 위험 고객(기본) · 문의 진단(보상 후보·결제 진단) · 급증·변경 요청. 모두 읽기 전용이고, 숫자는 서버가 센 값만 보여 준다.

const REFRESH_MS = 30_000;

type Mode = "risk" | "inquiry" | "surge";
const MODES: { id: Mode; label: string; hash: string }[] = [
  { id: "risk", label: "위험 고객", hash: "" },
  { id: "inquiry", label: "문의 진단", hash: "#inquiry" },
  { id: "surge", label: "급증·변경 요청", hash: "#surge" },
];

export default function SupportPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const mode: Mode = MODES.find((m) => m.hash && m.hash === location.hash)?.id ?? "risk";
  const params = new URLSearchParams(location.search);
  const buyer = params.get("buyer");
  const inquiryParam = Number(params.get("inquiry"));
  const inquiry = Number.isInteger(inquiryParam) && inquiryParam > 0 ? inquiryParam : null;

  const openMode = useCallback((m: Mode) => {
    navigate({ search: location.search, hash: MODES.find((x) => x.id === m)?.hash ?? "" }, { replace: true });
  }, [navigate, location.search]);
  /** 고른 고객·문의는 주소에 남겨 새로고침해도 같은 것을 본다 */
  const openBuyer = useCallback((id: string) => navigate({ search: `?buyer=${encodeURIComponent(id)}`, hash: "" }, { replace: true }), [navigate]);
  const openInquiry = useCallback((id: number) => navigate({ search: `?inquiry=${id}`, hash: "#inquiry" }, { replace: true }), [navigate]);

  // 30초마다 모두 새로 받는다
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const t = setInterval(bump, REFRESH_MS);
    return () => clearInterval(t);
  }, [bump]);

  return (
    <div className="w-full space-y-6 px-4 pb-12 pt-2 sm:space-y-8 sm:px-8">
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">AI 처리 내역</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            캣북스의 고객지원팀입니다. 이탈할 것 같은 고객을 먼저 찾고, 문의마다 보상 후보와 결제가 막힌 곳을 보여 주고, 문의가 갑자기 몰리는 분류와 기획개발로 보낸 변경 요청을 봅니다.
            이 화면은 보기만 하며, 보상·변경 요청을 보내는 일은 사람이 승인한 뒤 따로 합니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:ml-auto">
          <button className="btn-ghost" onClick={bump}>새로고침</button>
        </div>
      </header>

      <div role="tablist" aria-label="고객지원 화면" className="flex w-full gap-1 rounded-2xl bg-surface p-1 ring-1 ring-ink-100 sm:w-auto sm:max-w-xl">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="tab" id={`support-mode-${m.id}`} aria-selected={mode === m.id} aria-controls={`support-mode-panel-${m.id}`} onClick={() => openMode(m.id)}
            className={`flex-1 rounded-xl px-4 py-2.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${mode === m.id ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {m.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`support-mode-panel-${mode}`} aria-labelledby={`support-mode-${mode}`}>
        {mode === "risk" && <AtRisk tick={tick} selected={buyer} onSelect={openBuyer} />}
        {mode === "inquiry" && <InquiryCheck tick={tick} selected={inquiry} onSelect={openInquiry} />}
        {mode === "surge" && <SurgeChanges tick={tick} />}
      </div>
    </div>
  );
}
