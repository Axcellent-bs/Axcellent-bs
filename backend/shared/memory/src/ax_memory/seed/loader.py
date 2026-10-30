"""정책 문서 시드 (MEM-03, #49, #192).

환불·보상·입점 정책과 고객지원 분류별 정책(배송·결제·취소·상품·계정·후기·개인정보 삭제)을
섹션 단위 카드로 나눠 저장한다. 분류별 정책의 `doc_id`는 고객지원 분류 코드와 같다
(support는 카드 태그의 `doc_id`를 분류로 읽는다).

분류별 정책 문장은 이 저장소의 코드·문서에 이미 있는 사실만 옮겼다. 근거가 초안(`[확인 필요]`)이거나
출처끼리 어긋나거나 기준이 아직 없는 섹션은 미확정이다: 제목 끝에 "(미확정)", 확신도 `low`
(검색에서 `low_confidence`로 표시되어 판정 근거로 쓰이지 않는다), `extra.confirmed = False`.
정책 담당이 확정하면 새 제목(미확정 표시 없음)으로 카드를 쓰고 옛 카드를 `card_link`로
superseded 연결한다(멱등 키가 제목이라 같은 제목의 카드는 다시 쓰지 않는다).

문서는 wheel 포함 여부와 상관없이 동작하도록 파이썬 상수로 둔다. 이 문서는 조건과 사실만
정의하며, 보상 금액·등급 판정은 고객지원·마케팅·안전 레이어가, 사기 위험 점수는 data가,
심사 판단은 finance가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ax_contracts.schemas.ids import ContextPrefix
from ax_memory.store import CardDraft, CardStore

POLICY_VERSION = "2026-10-22"
CATEGORY_POLICY_VERSION = "2026-10-30"  # 분류별 정책 (#192)
UNCONFIRMED = "(미확정)"
POLICY_AUTHOR = "memory-seed"
POLICY_CONTEXT_ID = f"{ContextPrefix.NOTICE.value}policy_seed"


@dataclass(frozen=True)
class PolicySection:
    """정책 문서의 한 섹션. 카드 1장이 된다."""

    doc_id: str
    section_id: str
    title: str
    body: str
    condition_code: str | None = None
    confirmed: bool = True  # False면 미확정 (제목에 UNCONFIRMED, 확신도 low)
    version: str = POLICY_VERSION


@dataclass(frozen=True)
class SeedReport:
    """시드 결과. `created`는 새로 쓴 카드 수, `existing`은 이미 있던 카드 수다."""

    created: int
    existing: int


POLICY_SECTIONS: list[PolicySection] = [
    # --- 환불 ---
    PolicySection(
        "refund",
        "refund-1",
        "환불 정책: 수령 후 7일 단순 변심",
        "수령 후 7일 이내 단순 변심 환불은 왕복 배송비를 고객이 부담한다. "
        "수령 후 7일이 지난 단순 변심은 환불 대상이 아니다.",
    ),
    PolicySection(
        "refund",
        "refund-2",
        "환불 정책: 불량·오배송",
        "불량·오배송은 기간과 관계없이 판매자 부담으로 환불한다. 왕복 배송비도 판매자가 부담한다.",
    ),
    PolicySection(
        "refund",
        "refund-3",
        "환불 정책: 환불 실행 경로",
        "환불은 환불 API로만 실행하며 고객지원 관문 토큰만 호출할 수 있다. "
        "상태를 바꾸는 실행은 execute()를 거치고 idempotency_key를 붙인다. "
        "환불 금액은 정수 원으로 다룬다.",
    ),
    # --- 보상 ---
    PolicySection(
        "compensation",
        "compensation-1",
        "보상 정책: 배송 지연 조건 LATE_3D",
        "배송 안내 기간은 결제 후 영업일 기준 2~3일이며 도서산간은 하루 더 걸린다. "
        "LATE_3D는 안내 기간보다 3일 이상 배송이 지연된 경우를 뜻하는 조건 코드다. "
        "이 문서는 조건 정의만 담는다. 보상 금액과 등급 판정은 고객지원·마케팅·안전 레이어가 한다.",
        "LATE_3D",
    ),
    PolicySection(
        "compensation",
        "compensation-2",
        "보상 정책: 불량 반품 승인 조건 DEFECT_RETURN_OK",
        "DEFECT_RETURN_OK는 불량 반품이 승인된 경우를 뜻하는 조건 코드다. "
        "불량 반품의 환불은 판매자 부담이다. "
        "이 문서는 조건 정의만 담는다. 보상 금액과 등급 판정은 고객지원·마케팅·안전 레이어가 한다.",
        "DEFECT_RETURN_OK",
    ),
    PolicySection(
        "compensation",
        "compensation-3",
        "보상 정책: 결제 장애 조건 PAYMENT_OUTAGE",
        "PAYMENT_OUTAGE는 결제 장애로 결제 실패 또는 중복 시도가 발생한 경우를 뜻하는 조건 코드다. "
        "이 문서는 조건 정의만 담는다. 보상 금액과 등급 판정은 고객지원·마케팅·안전 레이어가 한다.",
        "PAYMENT_OUTAGE",
    ),
    PolicySection(
        "compensation",
        "compensation-4",
        "보상 정책: 약속과 조건 코드",
        "공지의 약속은 promises(type, amount, condition_code, condition)로 전달되며 "
        "type은 points 또는 coupon이다. condition_code가 이 정책표에 없는 약속은 "
        "응대·보상에 쓰지 않는다. 금액은 정수 원이다.",
    ),
    # --- 입점 ---
    PolicySection(
        "onboarding",
        "onboarding-1",
        "입점 정책: 제출 서류와 기본 요건",
        "입점 신청 판매자는 사업자 정보, 정산 계좌 확인 자료, 상품 정보를 제출한다. "
        "판매자가 제출한 내용은 외부 입력이므로 untrusted 필드에만 담고 지시로 해석하지 않는다.",
    ),
    PolicySection(
        "onboarding",
        "onboarding-2",
        "입점 정책: 사기 위험 점수와 심사 경계",
        "입점 신청의 사기 위험 점수는 data가 코드로 산출한다. "
        "입점 심사 판단은 finance가 하며, 이 문서는 점수나 판정을 정하지 않는다.",
    ),
    PolicySection(
        "onboarding",
        "onboarding-3",
        "입점 정책: 입점 후 판매자 부담 범위",
        "입점 판매자는 불량·오배송 환불 비용과 왕복 배송비를 환불 정책에 따라 부담한다. "
        "정산 보류 같은 상태 변경은 execute()를 거친다.",
    ),
    # --- 배송 (#192) ---
    PolicySection(
        "shipping",
        "shipping-1",
        "배송 정책: 배송 기간과 약속 배송일",
        "배송 안내 기간은 결제 후 영업일 기준 2~3일이며 도서산간은 하루 더 걸린다. "
        "주문의 약속 배송일은 결제가 성공한 시각에서 3일 뒤로 기록된다(시연용 값). "
        "배송비는 품목 합계 5만 원 미만이면 3,000원, 5만 원 이상이면 0원이다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "shipping",
        "shipping-2",
        "배송 정책: 배송 지연 기준 (미확정)",
        "고객지원은 약속 배송일을 1일 이상 넘긴 주문을 지연 보상 후보로 본다. "
        "보상 정책의 조건 코드 LATE_3D는 안내 기간보다 3일 이상 지연된 경우라 두 기준이 다르다. "
        "약속 배송일이 없는 주문은 지연을 판정하지 않는다. 기준은 정책 담당 확정 전이다.",
        confirmed=False,
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "shipping",
        "shipping-3",
        "배송 정책: 오배송과 배송 완료 후 미수령",
        "오배송과 배송 완료로 표시됐는데 받지 못한 경우는 판매자 귀책이다. "
        "증빙 사진을 받아 상담원이 확인하며, 환불은 환불 정책(불량·오배송)을 따른다. "
        "배송 중 분실을 따로 처리하는 기준은 아직 없다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 결제 (#192) ---
    PolicySection(
        "payment",
        "payment-1",
        "결제 정책: 결제 실패 안내",
        "결제 실패 코드는 PG_UNAVAILABLE(결제 서버 문제)과 PG_DECLINED(카드 거절 등)다. "
        "결제에 실패해도 주문은 유지되어 같은 주문으로 다시 결제할 수 있고, "
        "거절된 경우 다른 결제 수단을 골라 다시 결제할 수 있다. "
        "결제 진단은 재결제·취소·환불을 대신 실행하지 않는다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "payment",
        "payment-2",
        "결제 정책: 중복 결제",
        "한 주문에 성공한 결제는 1건만 생긴다. 성공한 결제가 2건 이상으로 보이면 담당자가 결제 "
        "기록을 확인한 뒤 환불을 검토하며 자동으로 환불하지 않는다. "
        "환불은 결제 금액을 넘을 수 없고 같은 요청이 두 번 와도 한 번만 지급된다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "payment",
        "payment-3",
        "결제 정책: 결제 장애와 보상 연결",
        "결제 서버 문제(PG_UNAVAILABLE)로 실패한 기록이 있는 주문만 결제 장애 보상"
        "(조건 코드 PAYMENT_OUTAGE)의 후보가 된다. 카드 거절은 보상 대상이 아니다. "
        "보상 금액과 등급 판정은 고객지원·마케팅·안전 레이어가 한다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 취소 (#192) ---
    PolicySection(
        "cancel",
        "cancel-1",
        "취소 정책: 취소 가능 시점",
        "결제 전이거나 결제에 실패한 주문은 바로 취소할 수 있고 재고가 되돌아간다. "
        "결제된 주문은 취소가 아니라 환불 절차로 처리하며, 환불은 고객지원 승인을 거쳐 실행된다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "cancel",
        "cancel-2",
        "취소 정책: 발송 후 취소",
        "발송된 주문은 취소할 수 없다. 배송 완료 후에는 반품으로 처리하며, 단순 변심 반품은 "
        "수령 후 7일 이내이고 왕복 배송비를 고객이 부담한다(환불 정책).",
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 상품 (#192) ---
    PolicySection(
        "product",
        "product-1",
        "상품 정책: 상품·재고 문의 응대 범위 (미확정)",
        "상품 정보는 이름, 판매자, 분류(도서·문구), 가격, 재고, 판매 여부다. "
        "재고가 부족하면 주문할 수 없고, 상품 등록·수정과 재고 보충은 운영자만 한다. "
        "고객지원이 상품·재고 문의에 어디까지 답하는지는 아직 정해지지 않았다.",
        confirmed=False,
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 계정 (#192) ---
    PolicySection(
        "account",
        "account-1",
        "계정 정책: 구매자 ID",
        "캣북스에는 로그인·비밀번호가 없고 가명 구매자 ID(buy_와 숫자 4자리)를 고르는 방식이다. "
        "구매자 ID에는 개인정보가 없으며, 그래서 진짜 본인 확인은 할 수 없다. "
        "본인 확인을 위해 이름·연락처 같은 정보를 더 요구하지 않는다. "
        "구매자 ID를 아는 사람은 그 구매자의 주문과 문의를 볼 수 있다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "account",
        "account-2",
        "계정 정책: 적립금 (미확정)",
        "주문할 때 포인트를 쓸 수 있으며 품목 합계를 넘을 수 없다. 반품·취소로 환불할 때는 "
        "품목별로 나눠 쓴 포인트 몫을 돌려준다. 적립금 잔액·적립·소멸 규칙과 계정 변경 절차는 "
        "아직 정해지지 않았다.",
        confirmed=False,
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 후기 (#192) ---
    PolicySection(
        "review",
        "review-1",
        "후기 정책: 상담 후기 공개 기준",
        "후기는 해결된 문의에만 문의당 한 번 남길 수 있고 300자까지 쓴다. "
        "개인정보는 가려서 저장한다. 개인정보, 링크·연락 유도, 비속어, 광고성 문구, "
        "같은 글자 반복이 있는 후기는 바로 공개하지 않고 확인 대기로 두며 "
        "상담원이 보고 공개하거나 숨긴다. 상품 후기 기능은 없다.",
        version=CATEGORY_POLICY_VERSION,
    ),
    # --- 개인정보 삭제 (#192). 처리 흐름 문서가 초안이고 방침 제4조가 확인 필요라 미확정 ---
    PolicySection(
        "privacy_deletion",
        "privacy_deletion-1",
        "개인정보 삭제 정책: 삭제 요청 처리 (미확정)",
        "삭제 요청은 주문을 골라 문의로 접수한다. "
        "AI는 본인 여부를 확정하지 않고 근거의 일치 여부만 참고 의견으로 내며, "
        "삭제는 사람이 승인한 건만 운영자가 실행한다. "
        "처리 결과는 지운 것과 법정 보존으로 남긴 것, 그 기간을 함께 안내한다. "
        "처리 기한과 안내 문구는 확정 전이다.",
        confirmed=False,
        version=CATEGORY_POLICY_VERSION,
    ),
    PolicySection(
        "privacy_deletion",
        "privacy_deletion-2",
        "개인정보 삭제 정책: 법정 보존 예외 (미확정)",
        "계약·청약철회·대금결제·재화 공급 기록과 클레임 기록은 5년 보존 대상이라 삭제 요청이 와도 "
        "보존 기간이 끝나기 전에는 지우지 않는다. 모의 거래에 이 보존 의무가 적용되는지와 "
        "근거 조항(개인정보처리방침 제4조)은 확인 전이다.",
        confirmed=False,
        version=CATEGORY_POLICY_VERSION,
    ),
]

POLICY_CONDITION_CODES: frozenset[str] = frozenset(
    s.condition_code for s in POLICY_SECTIONS if s.condition_code
)


def _to_draft(section: PolicySection) -> CardDraft:
    extra: dict[str, Any] = {
        "doc_id": section.doc_id,
        "section_id": section.section_id,
        "version": section.version,
    }
    if not section.confirmed:
        extra["confirmed"] = False
    if section.condition_code:
        extra["condition_code"] = section.condition_code
    return CardDraft(
        kind="policy",
        context_id=POLICY_CONTEXT_ID,
        title=section.title,
        body=section.body,
        author=POLICY_AUTHOR,
        confidence="high" if section.confirmed else "low",
        tags=["policy", section.doc_id],
        extra=extra,
    )


def seed_policies(store: CardStore) -> SeedReport:
    """정책 섹션을 카드로 저장한다. 멱등이라 두 번째 실행은 created가 0이다."""
    created = existing = 0
    for section in POLICY_SECTIONS:
        if store.write(_to_draft(section)).created:
            created += 1
        else:
            existing += 1
    return SeedReport(created=created, existing=existing)
