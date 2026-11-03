import { useState } from "react";
import { PreparingChip } from "@/components/Preparing";
import { finance, humanTokenSet, krw, plainText, signedKrw, todayKst, type TeamBudget } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ④ 팀 예산 (인사 ↔ 재무, 2026-11-02 #2-재무 합의).
// 팀 예산 = 그달 1일 팀 인원(인사 AI) × 인당 월 단가(회사 설정). 발령은 다음 달 1일부터, 파견은 세지 않는다.
// 재무 AI는 계산과 지난달 대비 증감만 보여 주고, 확정은 재무 담당(사람)이 누른다.

const STATUS_KO: Record<TeamBudget["status"], { label: string; tone: string }> = {
  pending: { label: "확정 전", tone: TONE.warn },
  approved: { label: "확정됨", tone: TONE.ok },
  changed: { label: "확정 뒤 숫자가 바뀜", tone: TONE.bad },
  unavailable: { label: "인원을 받지 못함", tone: TONE.muted },
};

function shiftMonth(month: string, n: number): string {
  const [y, m] = month.split("-").map(Number);
  const k = y * 12 + (m - 1) + n;
  return `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, "0")}`;
}

/** 늘면 +, 줄면 − */
const delta = (n: number) => (n > 0 ? `+${krw(n)}` : signedKrw(n));
const people = (n: number | null) => (n == null ? "—" : `${n.toLocaleString("ko-KR")}명`);

export function TeamBudgetSection({ tick }: { tick: number }) {
  const thisMonth = todayKst().slice(0, 7);
  const [month, setMonth] = useState(thisMonth);
  const res = useResource(`team-budget:${month}`, () => finance.teamBudget(month), tick);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  async function approve(b: TeamBudget) {
    if (!b.signature) return;
    setBusy(true);
    setProblem(null);
    try {
      const out = await finance.approveTeamBudget(b.month, b.signature, note.trim());
      res.set(() => out);
      setNote("");
    } catch (e) {
      setProblem((e as Error).message);
      await res.reload();
    } finally {
      setBusy(false);
    }
  }

  const months = [shiftMonth(thisMonth, -1), thisMonth, shiftMonth(thisMonth, 1)];
  return (
    <Panel id="team-budget" title="팀 예산" meta={res.env?.meta}
      hint="팀 예산 = 그달 1일 팀 인원 × 인당 월 단가입니다. 인원은 인사 AI가 알려 준 숫자이고(이름은 받지 않음), 발령은 다음 달 1일부터 반영됩니다. 파견은 원래 팀으로 셉니다. 재무 AI는 계산만 하고 확정은 재무 담당이 합니다."
      tools={
        <div className="flex gap-1 rounded-full bg-ink-50 p-0.5" role="group" aria-label="달 고르기">
          {months.map((m) => (
            <button key={m} type="button" aria-pressed={m === month} onClick={() => setMonth(m)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${m === month ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
              {m === thisMonth ? `${m} (이번 달)` : m}
            </button>
          ))}
        </div>
      }>
      <ResourceBody res={res}>
        {(b) => {
          const s = STATUS_KO[b.status];
          if (b.status === "unavailable") {
            return (
              <Empty>
                인사 AI에서 {b.month} 팀 인원을 받지 못해 팀 예산을 계산하지 않았습니다. 0원으로 채우지 않습니다.
                인사 AI의 팀 인원 조회(team_headcount)가 연결되면 여기에 나타납니다.
              </Empty>
            );
          }
          return (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                {b.example ? <Chip label="예시 인원" tone={TONE.muted} /> : <Chip label={s.label} tone={s.tone} />}
                <span className="text-ink-700">인당 월 <b className="tabular-nums">{krw(b.per_head_monthly)}</b> · 총 {people(b.headcount_total)}</span>
                <span className="text-ink-900">합계 <b className="tabular-nums">{krw(b.total)}</b></span>
                {b.change_total != null && <span className="text-xs tabular-nums text-ink-500">{b.prev_month} 대비 {b.change_total === 0 ? "변동 없음" : delta(b.change_total)}</span>}
                {b.basis && <span className="text-xs text-ink-500">{plainText(b.basis)}</span>}
              </div>

              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead className="text-left text-xs text-ink-500">
                    <tr>
                      <th className="py-2 pr-3 font-medium">팀</th>
                      <th className="py-2 pr-3 text-right font-medium">{b.prev_month} 인원</th>
                      <th className="py-2 pr-3 text-right font-medium">{b.month} 인원</th>
                      <th className="py-2 pr-3 text-right font-medium">예산</th>
                      <th className="py-2 text-right font-medium">지난달 대비</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-100">
                    {b.rows.map((r) => (
                      <tr key={r.team}>
                        <td className="py-2 pr-3 text-ink-900">{r.team}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-500">{people(r.prev_headcount)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-900">{people(r.headcount)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-900">{krw(r.budget)}</td>
                        <td className={`py-2 text-right tabular-nums ${r.change ? (r.change > 0 ? "text-emerald-700" : "text-red-700") : "text-ink-500"}`}>
                          {r.change == null ? "확인할 수 없음" : r.change === 0 ? "변동 없음" : delta(r.change)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {b.prev_total == null && <p className="text-xs text-ink-500">{b.prev_month} 인원을 받지 못해 지난달 대비는 보여 주지 않습니다.</p>}

              {b.approved && (
                <p className="text-xs text-ink-500">
                  {b.approved.by}님이 {new Date(b.approved.at).toLocaleString("ko-KR")}에 합계 {krw(b.approved.total)}(인당 {krw(b.approved.per_head_monthly)})로 확정함
                  {b.approved.note && ` · 메모: ${plainText(b.approved.note)}`}
                </p>
              )}

              {b.example && (
                <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  인사 AI가 실제 명부가 아니라 가상 명부(예시) 인원을 보냈습니다. 계산과 증감만 보여 주고 팀 예산으로 확정하지 않습니다.
                  실제 명부·발령 기록이 연결되면 확정할 수 있습니다.
                </p>
              )}

              {!b.example && (b.status === "pending" || b.status === "changed") && (
                <div className="space-y-2 rounded-2xl border border-ink-100 px-4 py-3">
                  <p className="text-xs text-ink-500">
                    {b.status === "changed"
                      ? "확정한 뒤 팀 인원이나 인당 단가가 바뀌었습니다. 지금 숫자를 보고 다시 확정하세요."
                      : "숫자가 맞으면 확정하세요. 이 달 팀 예산을 기준으로 남길 뿐, 돈을 옮기지는 않습니다."}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <input className="field min-w-0 flex-1" value={note} maxLength={500} placeholder="메모 (선택) 예: 12월 발령 반영 확인"
                      onChange={(e) => setNote(e.target.value)} aria-label="확정 메모" />
                    <button type="button" className="btn-primary" disabled={!humanTokenSet || busy} onClick={() => void approve(b)}>
                      {busy ? "확정하는 중…" : `${b.month} 팀 예산 확정`}
                    </button>
                  </div>
                  {!humanTokenSet && <p className="text-xs text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 없어 확정 버튼이 꺼져 있습니다.</p>}
                </div>
              )}
              {problem && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}

              <div className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
                <PreparingChip />
                <span>예산 대비 실제 지출: 팀별 지출 자료가 아직 연결되지 않아 비교하지 않습니다. 지어낸 숫자로 채우지 않습니다.</span>
              </div>
            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}
