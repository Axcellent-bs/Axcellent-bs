import { useState } from "react";
import { api } from "@/lib/api";

/**
 * 가린 고객 메시지의 원문 열기. 원문은 응답에 없어서 처음 열 때 서버에서 받고,
 * 그때 연 사람이 문의의 AI 처리 내력에 남는다. 한 번 받으면 다시 부르지 않는다.
 */
/** 원문을 열면 보내는 신호. 열람이 문의의 AI 처리 내력에 남으니, 화면이 문의를 다시 읽어 바로 보이게 한다. */
export const ORIGINAL_OPENED = "original-opened";

export function useOriginal(inquiryId: number, messageId: number) {
  const [show, setShow] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [failed, setFailed] = useState(false);
  const toggle = async () => {
    if (show) return setShow(false);
    if (text == null) {
      setOpening(true);
      setFailed(false);
      try {
        setText((await api.admin.revealOriginal(inquiryId, messageId)).text);
        window.dispatchEvent(new CustomEvent(ORIGINAL_OPENED, { detail: { inquiryId } }));
      } catch {
        setFailed(true);
        return;
      } finally {
        setOpening(false);
      }
    }
    setShow(true);
  };
  return { show, text, opening, failed, toggle };
}
