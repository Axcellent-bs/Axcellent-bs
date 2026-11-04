"""캐시 키에 넣는 코드 버전: 그 결과를 만드는 모듈 소스의 해시.

캐시 키가 입력(소개·카탈로그·설정·프롬프트)만 보면 코드가 바뀌어도 예전 결과를 다시 쓴다
(#598 뒤 캣북스 프로필이 씨앗을 빼지 않은 채 남았던 문제). 관련 모듈 소스를 해시해 키에 넣으면
코드가 바뀐 다음 요청에서 다시 만든다. 줄바꿈(CRLF·LF)은 같게 본다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SKILLS = Path(__file__).resolve().parent


def source_hash(*names: str) -> str:
    """`skills/<이름>.py` 소스들의 해시 (12자). 파일을 못 읽으면 그 이름만 넣는다."""
    digest = hashlib.sha256()
    for name in names:
        path = SKILLS / f"{name}.py"
        try:
            text = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            text = f"missing:{name}".encode()
        digest.update(name.encode() + b"\0" + text + b"\0")
    return digest.hexdigest()[:12]
