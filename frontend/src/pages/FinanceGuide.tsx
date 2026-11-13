import type { ReactNode } from "react";
import { Link } from "@/lib/nav";
import { CompanyName } from "@/demo/CompanyName";

// 재무 AI 관리자 사용법. 기준 문서: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md (§12 장애 이벤트 재무 + 재무팀 모듈)
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
        <thead className="text-left text-sm text-ink-500">
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
          재무 AI는 <b><CompanyName />의 재무팀</b>입니다. 화면은 두 탭입니다.
          기본으로 열리는 <b>재무 장애 이슈</b>는 장애 때 나간 <b>보상 쿠폰과 환불 금액</b>을 이슈별 장부로 보여 줍니다.
          <b>평상시 재무 업무</b>에는 ① 결산·리포트부터 ⑦ 자금·현금 흐름까지 일곱 개 업무 탭이 있습니다. 고칠 점은 화면 위 <b>기획에 개선 요청</b> 버튼으로 어느 탭에서든 보냅니다.
          <b>재무는 쿠폰·환불의 끝난 금액을 장부에 적기만 합니다.</b>
        </p>
      </header>

      <Part title="처음이라면: 이 순서대로">
        <ol className="list-decimal space-y-1.5 pl-5">
          <li><b>재무 AI 테스트 확인</b>: 화면 위쪽 테스트 결과가 초록색(정상)인지 봅니다. 서버가 10분마다 스스로 테스트하므로 누를 필요는 없습니다.</li>
          <li><b>지금 사람이 할 일</b>: 노란 상자에 항목이 있으면 눌러서 해당 화면으로 갑니다.</li>
          <li><b>이슈 고르기</b>: 재무 장애 이슈 탭 위쪽 이슈 탭에서 이슈를 고릅니다. 가장 최근 이슈가 먼저 열려 있습니다.</li>
          <li><b>장부 확인</b>: 그 이슈의 결제 실패(미결제), 확인·환불 필요 금액, 보상 쿠폰, 환불로 빠져나간 돈을 나눠 봅니다.</li>
          <li><b>보류 큐 결정</b>: 재무가 정할 보류 건은 화면 맨 아래 <b>안전 장치 보류 큐</b>에서 진행 또는 멈춤을 누릅니다. 판매자 정산 지급 승인은 <Link href="/settlements" className="font-medium text-brand-700 underline">고객지원 &gt; 판매자 정산</Link>에서 합니다.</li>
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
            ["판매자 정산", "판매 대금 지급 기한 임박", "고객지원 > 판매자 정산 (재무 ③은 보기만)"],
            ["결산 · 대사", "결산 확정 대기, PG 금액이 다른 결제", "① 결산·리포트, ② 매출·PG 대사"],
                        ["세무", "확정한 부가세 숫자가 달라짐", "⑤ 세무 지원"],
            ["보류 큐", "안전 장치가 멈춰 둔 재무 결정", "화면 맨 아래 안전 장치 보류 큐 (두 탭 공통)"],
          ]}
        />
      </Part>

      <Part title="재무 장애 이슈 (기본 탭)">
        <H3>이슈 고르기</H3>
        <p>위쪽 탭에 <b>최근 이슈 5개</b>가 <b>장애 #번호 + 날짜</b>로 나옵니다(휴대폰에서는 2개). 더 오래된 이슈는 탭 끝의 <b>이전 이슈 N건</b> 목록에서 고릅니다. 탭 옆 주황 점은 결정을 기다리는 보상 쿠폰이 있다는 뜻입니다.</p>
        <p>탭 아래에 고른 이슈의 내용이 나옵니다. 맨 위에 <b>결제 실패(미결제)와 보상으로 나간 돈</b>이 있습니다.</p>

        <H3>이슈 장부에 적는 것</H3>
        <Table
          head={["항목", "내용"]}
          rows={[
            ["결제 실패(미결제)", "결제가 실패해 고객 돈이 나가지 않은 금액(받지 못한 매출)입니다. 데이터 AI가 센 숫자 그대로이고 재무가 다시 계산하지 않습니다"],
            ["확인·환불 필요", "결제사에 요청은 갔는데 응답이 끊겨 승인됐는지 모르는 결제입니다. 고객 돈이 빠져나갔을 수 있어 실제 피해가 될 수 있습니다. 판매 서버가 결제사에 다시 확인하고, 필요하면 고객지원이 환불합니다"],
            ["보상으로 나간 돈", "마케팅 AI가 준 보상 쿠폰의 금액 합계와 내역입니다. 출처: 안전 장치 실행 기록(있으면 이것이 원본) / 쿠폰 지급 알림(실행 기록이 없을 때) / 예시 데이터"],
            ["환불로 나간 돈", "이슈 구간에 영향 고객에게 나간 환불 금액 합계입니다. 구간이나 영향 고객을 모르면 0원이 아니라 확인할 수 없음으로 둡니다"],
          ]}
        />
      </Part>

      <Part title="평상시 재무 업무 (두 번째 탭)">
        <p>위쪽 <b>오늘의 재무</b> 카드(총거래액, 판매 수수료 매출, 환불, 쿠폰 비용, PG 수수료, 영업 결과) 아래에 모듈 탭이 있습니다.</p>
        <Table
          head={["모듈", "하는 일", "사람이 할 일"]}
          rows={[
            ["① 결산·리포트", "월 마감 체크리스트, 일·주·월 손익 요약과 분개 미리보기, 월간 변동 분석, 재무제표(장부 밖 손익 입력 포함), 고객지원 비용 기록(환불·쿠폰·배송비, 읽기 전용)", "숫자 확인 후 결산 확정 (돈은 움직이지 않음)"],
            ["② 매출·PG 대사", "주문 기록과 PG 정산 자료 대사(PG 정산 파일 올리기), 은행 입금 대조(은행 거래내역 올리기·읽은 기록), 은행 출금 대조(항목별 출금·설명 없는 출금)", "다른 건마다 처리 메모, 설명 없는 출금에 이유 적기"],
            ["③ 판매자 정산", "고객지원이 계산한 정산금(판매액 − 수수료 − 판매자 부담 쿠폰 − 환불)과 묶음 상태, 판매 대금 지급 기한, 판매자 미지급금 롤포워드, 판매자 정산 이체 확인", "보기만 함. 지급 승인 요청·승인은 고객지원 > 판매자 정산에서"],
            ["④ 팀 예산", "팀 예산 = 그달 1일 팀 인원 × 인당 월 단가, 지난달 대비 증감", "숫자를 봅니다. 확정은 인사 AI가 하고 재무는 기록만 합니다"],
            ["⑤ 세무 지원", "부가세 신고 보조 집계, 판매자별 판매 명세 초안, 법인세 참고 추정, 자동으로 만든 파일(세무사 전달용), 회계 기준 확인 목록", "분기 숫자 확정, 파일을 내려받아 세무사에게 전달"],
            ["⑥ 캠페인 재무", "캠페인 손익, 예산 의견, 실패 교훈, 재무 인사이트", "참고"],
            ["⑦ 자금·현금 흐름", "앞으로 13주 들어올 돈·나갈 돈, 통장 잔액에서 시작한 예상 잔액", "현금이 모자랄 주가 있는지 보기"],
            ["탭 밖: 총괄이 재무에 맡긴 일", "재무 AI 테스트 아래 늘 보임. 총괄이 재무에 맡긴 일과 결과. 최근 7일 실패는 지금 할 일에도 올라옵니다", "실패가 있으면 무엇이 실패했는지 확인"],
            ["탭 밖: 기획에 개선 요청", "늘 펼쳐 둠. 보낸 요청 목록·요청 빼기, 기획이 되물은 질문에 답하기. 화면 위 버튼과 대사 예외·기한 지난 마감·테스트 실패 옆 \"이걸로 개선 요청\"은 이 섹션으로 내려가 근거를 미리 채웁니다", "고칠 점이 있으면 근거와 함께 요청, 기획 질문에 답"],
          ]}
        />
      </Part>


      <Part title="자주 묻는 질문">
        <Q q="재무 AI 테스트를 아침마다 눌러야 하나요?">아니요. 서버가 10분마다 자동으로 합니다. "자동 테스트 결과가 늦어지고 있습니다"가 보일 때만 재무 AI 서버를 확인하세요.</Q>
        <Q q="오래된 장애 이슈는 어디 있나요?">이슈 탭 끝의 <b>이전 이슈 N건</b> 목록에 있습니다.</Q>
        <Q q="장애 이슈 목록이 비어 있어요.">아직 재무가 아는 장애 이슈가 없다는 뜻입니다. 총괄 AI가 이슈(<code>ctx_inc_…</code>)를 열고 보상 쿠폰이 나가면 나타납니다.</Q>
        <Q q="미결제 금액과 실제 피해는 어떻게 달라요?">미결제는 결제가 실패해 고객 돈이 나가지 않은 금액이라 고객이 다시 결제하면 회수됩니다. 실제 피해가 될 수 있는 돈은 "확인·환불 필요"(시간 초과 결제)와 ② 대사의 "우리 기록 없음"입니다. 미결제 숫자가 이상하면 데이터 담당자에게 문의하세요.</Q>
        <Q q="은행 거래내역은 어디서 올리나요?">② 매출·PG 대사 탭의 <b>은행 입금 대조</b>에서 올립니다. 같은 파일로 은행 출금 대조(②), 판매자 정산 이체(③), 13주 현금 흐름의 통장 잔액(⑦)이 함께 맞춰집니다. 잘못 올린 기록은 바로 아래 <b>은행 거래내역 읽은 기록</b>에서 뺍니다.</Q>
        <Q q="결산 확정·메모·결정 버튼이 꺼져 있어요.">사람 권한이 설정되지 않은 것입니다. 데모에서는 <code>frontend/.env</code>에 <code>VITE_SAFETY_HUMAN_TOKEN=human:이름</code>을 넣고 개발 서버를 다시 켭니다.</Q>
        <Q q="&quot;예시 데이터&quot; 표시가 있어요.">재무 AI가 연습용 자료를 보여 주는 중입니다. 실제 쇼핑몰 숫자가 아닙니다.</Q>
        <Q q="&quot;재무 AI 서버(8105)에 연결하지 못했습니다&quot;가 보여요.">ax-backend에서 <code>docker compose -f infra/compose.yml up -d safety finance</code>로 켭니다. 안전 장치가 꺼져 있는 동안 돈이 나가는 일은 모두 멈춥니다.</Q>
      </Part>

      <p className="text-sm text-ink-500">문의: 재무/정산 담당 태윤</p>
    </div>
  );
}
