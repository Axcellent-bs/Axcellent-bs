import type { ConversationListItem } from "@/lib/types";

// 판매 서버 자료를 다시 만들기 전에 받은 문의("옛 자료")는 문의에 든 주문이 판매 서버에 없을 수 있다.
// 고객지원이 접수일과 설정값만 비교해 `legacy_intake`를 붙여 주고(상태·재무는 그대로),
// 화면은 이 문의를 기본 목록에서 숨기고 필터로만 보여 준다. 주문이 실제로 있는지는 상세의 주문 조회가 말한다.
// 문구는 "접수 시점"만 말한다: 주문이 실제로 있는 문의에 "주문 없음"으로 읽히지 않게.
export const LEGACY_LABEL = "판매 서버 자료 재생성 전 접수";
export const LEGACY_HINT = "이 문의에 든 주문이 판매 서버에 없을 수 있어요. 상세의 주문 조회가 실제 여부를 알려 줘요.";

export const isLegacy = (c: { legacy_intake?: boolean }) => c.legacy_intake === true;

/** 기본은 옛 자료를 뺀다. `showLegacy`를 켜면 전부 보인다. `legacyCount`는 켜고 끄는 것과 상관없이 목록에 든 옛 자료 수 */
export function splitLegacy<T extends Pick<ConversationListItem, "legacy_intake">>(list: T[], showLegacy: boolean) {
  const legacyCount = list.filter(isLegacy).length;
  return { shown: showLegacy || !legacyCount ? list : list.filter((c) => !isLegacy(c)), legacyCount };
}
