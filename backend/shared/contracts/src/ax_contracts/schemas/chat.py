"""부서 AI 직접 대화(`<부서>_chat_*`)가 같이 쓰는 계약.

- `ChatRemoveOutput`: `<부서>_chat_remove` 결과. 요청한 사람(`who`) 본인의 대화를 저장소에서 지운다.
  입력은 부서마다 있는 `<부서>ChatRef`(who·thread_id)를 그대로 쓴다.
  거절 outcome: `not_found`(없는 대화) · `forbidden`(남의 대화).
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel


class ChatRemoveOutput(AxModel):
    thread_id: str = Field(min_length=1, max_length=64)
    removed: bool
