import type { ReactNode } from "react";
import { Link } from "@/lib/nav";

// 재무 AI 관리자 사용법. 기준 문서: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md (§12 장애 사건 재무 + 재무팀 모듈)
// 원본 사용법 문서(ax-backend docs/notes/finance/admin-guide.md)도 같은 내용으로 고쳐야 한다.

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-ink-900">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-ink-700">{children}</div>
    </section>
  );
}

const H3 = ({ children }: { children: ReactNode }) => <h3 className="pt-2 font-semibold text-ink-900">{children}</h3>;

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="card overflow-x-auto p-1">
      <table className="w-full min-w-[520px] text-sm">
        <thead className="text-left text-xs text-ink-500">
          <tr>{head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-ink-100 align-top">
              {r.map((c, j) => <td key={j} className={`px-3 py-2 ${j === 0 ? "font-medium text-ink-900" : ""}`}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const Q = ({ q, children }: { q: string; children: ReactNode }) => (
  <div className="card p-4">
    <p className="font-semibold text-ink-900">{q}</p>
    <div className="mt-1 text-sm text-ink-700">{children}</div>
  </div>
);

export default function FinanceGuidePage() {
  return (
    <div className="w-full space-y-10 px-4 pb-14 pt-2 sm:px-8">
      <header>
        <Link href="/finance" className="text-sm text-brand-700 hover:underline">← 재무 AI로 돌아가기</Link>
        <h1 className="mt-3 text-2xl font-bold tracking-tight">재무 AI 관리자 사용법</h1>
        <p className="mt-3 rounded-2xl bg-brand-50 px-4 py-3 text-sm leading-relaxed text-brand-700">
          재무 AI는 <b>캣북스의 재무팀</b>입니다. 화면은 두 탭입니다.
          기본으로 열리는 <b>장애 사건 재무</b>는 장애가 났을 때 <b>⑨ 보상 쿠폰을 장부에 적고 → ⑩ 손실(피해 + 보상)을 정리하고 → ⑰ 끝난 뒤 감사하는</b> 일을 사건별로 보여 줍니다.
          <b>평상시 재무 업무</b>에는 결산, 대사, 판매자 정산, 한도 제안, 사후 감사가 있습니다.
          쿠폰·환불을 한 건씩 승인하지는 않습니다.
        </p>
      </header>

      <Part title="처음이라면: 이 순서대로">
        <ol className="list-decimal space-y-1.5 pl-5">
          <li><b>재무 AI 테스트 확인</b>: 화면 위쪽 테스트 결과가 초록색(정상)인지 봅니다. 서버가 10분마다 스스로 테스트하므로 누를 필요는 없습니다.</li>
          <li><b>지금 사람이 할 일</b>: 노란 상자에 항목이 있으면 눌러서 해당 화면으로 갑니다.</li>
          <li><b>사건 고르기</b>: 장애 사건 재무 탭 위쪽 사건 탭에서 사건을 고릅니다. 가장 최근 사건이 먼저 열려 있습니다.</li>
          <li><b>손실·감사 확인</b>: 총 손실(= 피해 + 보상)과 ⑰ 사건 후 감사 결과를 봅니다. ✗가 있으면 관련 기록 번호를 재무 담당자에게 알립니다.</li>
          <li><b>보류 큐 결정</b>: 판정이 C인 쿠폰은 화면 맨 아래 <b>안전 장치 보류 큐</b>에서 진행 또는 멈춤을 누릅니다.</li>
        </ol>
      </Part>

      <Part title="재무 AI 테스트 (자동, 10분마다)">
        <p>재무 AI 서버가 <b>10분마다 스스로</b> 가짜 자료로 계산과 연결을 점검합니다. 실제 기록이나 돈에는 영향이 없습니다. 화면은 1분마다 최근 결과를 다시 받아 <b>결과, 몇 분 전에 했는지, 자동/직접, 다음 자동 테스트 시각</b>을 보여 줍니다.</p>
        <Table
          head={["표시", "뜻", "할 일"]}
          rows={[
            [<span key="p" className="text-emerald-700">정상 (초록)</span>, "계산과 연결이 모두 정상", "없음"],
            [<span key="w" className="text-amber-700">주의 (노랑)</span>, "계산은 정상이지만 일부 연결(쇼핑몰·안전 장치 등)이 안 됨", "해당 서버 확인. 그동안 일부 숫자가 '확인할 수 없음'으로 보임"],
            [<span key="f" className="text-red-700">실패 (빨강)</span>, "계산이 틀리거나 꼭 필요한 연결이 끊김", <b key="fb">숫자를 믿지 말고 재무 담당자(태윤)에게 알림</b>],
            ["자동 테스트 결과가 늦어지고 있습니다", "최근 결과가 20분 넘게 없음", "재무 AI 서버가 켜져 있는지 확인"],
          ]}
        />
        <p>바로 확인하고 싶으면 <b>지금 다시 테스트</b>를 누릅니다(선택). 누르지 않아도 됩니다.</p>
      </Part>

      <Part title="지금 사람이 할 일">
        <p>화면 위쪽(두 탭 공통)에 재무 AI가 맞춰 보고 남은 일만 나옵니다. 항목을 누르면 처리할 곳으로 이동합니다.</p>
        <Table
          head={["종류", "예", "어디서 처리"]}
          rows={[
            ["보류 쿠폰", "장애 #… 보상 쿠폰이 보류 큐에서 결정을 기다림", "안전 장치 보류 큐"],
            ["사건 감사", "장애 #… 사건 후 감사에서 확인할 점이 나옴", "장애 사건 재무 → ⑰ 사건 후 감사"],
            ["판매자 정산", "판매자 정산 지급 승인 대기", "③ 판매자 정산 → 보류 큐"],
            ["결산 · 대사", "결산 확정 대기, PG 금액이 다른 결제", "① 결산·리포트, ② 매출·PG 대사"],
            ["감사 · 한도", "반복 환불 의심, 쿠폰 예산 거의 다 씀", "⑦ 내부통제·감사, ⑤ 예산·한도"],
          ]}
        />
      </Part>

      <Part title="장애 사건 재무 (기본 탭)">
        <H3>사건 고르기</H3>
        <p>위쪽 탭에 <b>최근 사건 5개</b>가 <b>장애 #번호 + 날짜</b>로 나옵니다(휴대폰에서는 2개). 더 오래된 사건은 탭 끝의 <b>이전 사건 N건</b> 목록에서 고릅니다. 탭 옆 점은 <span className="text-red-700">빨강 = 감사에서 확인할 점</span>, <span className="text-amber-700">주황 = C 보류 쿠폰 있음</span>입니다. 고른 사건은 주소(<code>?incident=</code>)에 남아 새로고침해도 그대로입니다. 키보드는 ← → 로 탭을 옮깁니다.</p>
        <p>탭 아래에 고른 사건의 내용이 나옵니다. 맨 위에 <b>총 손실, 피해 + 보상, 감사 상태</b>가 있습니다.</p>

        <H3>재무가 맡은 단계</H3>
        <Table
          head={["단계", "하는 일"]}
          rows={[
            ["⑨ 쿠폰 장부", "마케팅 AI가 준 보상 쿠폰을 사건별 장부에 적습니다. 출처: 이벤트(쿠폰 지급 알림) / 안전 장치 처리 기록(알림을 못 받을 때) / 예시 데이터"],
            ["⑩ 손실 리포트", "총 손실 = 피해(데이터 AI가 센 숫자 그대로) + 지급된 보상. 총괄 AI가 요청하면 보냅니다. 보류 중인 쿠폰은 아직 나간 돈이 아니라 넣지 않습니다"],
            ["⑰ 사건 후 감사", "사건이 끝나면 보상이 규칙대로 나갔는지 아래 5가지를 점검합니다"],
          ]}
        />
        <p>각 단계는 <b>끝남 / 기다리는 중 / 확인할 수 없음</b>으로 표시합니다. 보상 한도 사용(사건 전체, 고객 한 명 최대)도 막대로 보여 줍니다.</p>

        <H3>⑰ 사건 후 감사 5가지</H3>
        <Table
          head={["점검", "✗가 나오면"]}
          rows={[
            ["보상 받은 고객이 모두 영향 고객인가", "피해가 없는 사람이 쿠폰을 받음"],
            ["한 사건에 한 고객 쿠폰 1건인가", "같은 사람이 두 번 받음"],
            ["고객당 한도를 지켰나", "한 사람에게 한도보다 많이 줌"],
            ["사건당 한도를 지켰나", "사건 하나에 한도보다 많이 줌"],
            ["금액 재계산", "장수 × 한 장 금액이 적힌 금액과 다름"],
          ]}
        />
        <p>✗ 옆의 관련 기록 번호를 재무 담당자에게 알리면 됩니다. 한 장씩 막는 것이 아니라 끝난 뒤 다시 확인하는 일입니다.</p>

        <H3>쿠폰 판정 A / C 와 보류 큐</H3>
        <Table
          head={["판정", "뜻"]}
          rows={[
            ["A 바로 지급", "안전 장치가 보기에 한도 안이고 금액이 맞음 → 바로 지급"],
            ["C 사람 확인", "한도를 넘거나 금액이 맞지 않음 → 안전 장치 보류 큐로 가서 사람이 진행/멈춤을 정함"],
          ]}
        />
        <p>보상 쿠폰 받은 고객 표에서 C·보류 줄의 <b>보류 큐에서 결정</b>을 누르면 화면 맨 아래 보류 큐로 갑니다. <b>10분 안에 아무도 정하지 않으면 자동으로 취소</b>됩니다.</p>
      </Part>

      <Part title="평상시 재무 업무 (두 번째 탭)">
        <p>위쪽 <b>오늘의 재무</b> 카드(총거래액, 판매 수수료 매출, 환불, 쿠폰 비용, PG 수수료, 영업 결과) 아래에 모듈 탭이 있습니다.</p>
        <Table
          head={["모듈", "하는 일", "사람이 할 일"]}
          rows={[
            ["① 결산·리포트", "하루·한 달 손익 요약, 분개 미리보기, 쉬운 말 설명", "숫자 확인 후 결산 확정 (돈은 움직이지 않음)"],
            ["② 매출·PG 대사", "주문 기록과 PG 정산 자료를 맞춰 보고 다른 건만 보여 줌", "건마다 처리 메모 남기기"],
            ["③ 판매자 정산", "정산금 = 판매액 − 수수료 − 판매자 부담 쿠폰 − 환불. 문제 있는 판매자는 보류", "정산 묶음 지급 승인 요청 → 보류 큐에서 진행"],
            ["⑤ 예산·한도", "한도와 사용량, 한도를 바꾸자는 제안", "제안을 보고 바꿀지 사람이 정함"],
            ["⑦ 내부통제·감사", "지난 7·14·30일 기록에서 수상한 패턴 찾기(쪼개기, 반복 환불, 중복 쿠폰 등)", "위험 높음부터 조사해 재무 담당자에게 알림"],
          ]}
        />
      </Part>

      <Part title="재무가 하지 않는 일">
        <Table
          head={["일", "누가"]}
          rows={[
            ["쿠폰·환불 건별 승인", "안전 장치 (A 바로 지급 / C 보류)"],
            ["보상 단가·상한 정하기", "고객지원 정책표, 팀이 확정"],
            ["금액 한도 바꾸기", "사람이 바꾸는 기준표 (재무 AI는 제안만)"],
            ["쿠폰 지급", "마케팅 AI"],
          ]}
        />
        <p>숫자는 모두 코드가 계산하고, AI는 설명 문장만 씁니다. 받지 못한 값은 짐작하지 않고 <b>"확인할 수 없음"</b>으로 표시합니다.</p>
      </Part>

      <Part title="자주 묻는 질문">
        <Q q="재무 AI 테스트를 아침마다 눌러야 하나요?">아니요. 서버가 10분마다 자동으로 합니다. "자동 테스트 결과가 늦어지고 있습니다"가 보일 때만 재무 AI 서버를 확인하세요.</Q>
        <Q q="오래된 장애 사건은 어디 있나요?">사건 탭 끝의 <b>이전 사건 N건</b> 목록에 있습니다.</Q>
        <Q q="장애 사건 목록이 비어 있어요.">아직 재무가 아는 장애 사건이 없다는 뜻입니다. 총괄 AI가 사건(<code>ctx_inc_…</code>)을 열고 보상 쿠폰이 나가면 나타납니다.</Q>
        <Q q="피해 금액이 이상해 보여요.">피해 숫자는 데이터 AI의 영향 분석 그대로입니다. 데이터 담당자에게 문의하세요.</Q>
        <Q q="결산 확정·메모·결정 버튼이 꺼져 있어요.">사람 권한이 설정되지 않은 것입니다. 데모에서는 ax-frontend 루트 <code>.env</code>에 <code>VITE_SAFETY_HUMAN_TOKEN=human:이름</code>을 넣고 대시보드를 다시 켭니다.</Q>
        <Q q="&quot;예시 데이터&quot; 표시가 있어요.">재무 AI가 연습용 자료를 보여 주는 중입니다. 실제 쇼핑몰 숫자가 아닙니다.</Q>
        <Q q="&quot;재무 AI 서버(8105)에 연결하지 못했습니다&quot;가 보여요.">ax-backend에서 <code>docker compose -f infra/compose.yml up -d safety finance</code>로 켭니다. 안전 장치가 꺼져 있는 동안 돈이 나가는 일은 모두 멈춥니다.</Q>
      </Part>

      <p className="text-xs text-ink-500">문의: 재무/정산 담당 태윤</p>
    </div>
  );
}
