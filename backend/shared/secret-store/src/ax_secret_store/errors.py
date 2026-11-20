"""사유 코드만 담는 예외. 메시지·로그에 비밀값을 절대 넣지 않는다."""

from __future__ import annotations


class SecretStoreError(Exception):
    """`code`(짧은 영문 사유)만 가진다. 값·키·경로 같은 세부는 넣지 않는다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:  # 항상 코드만
        return self.code


# 시작 거부·운영 중 읽기 실패에 쓰는 코드
MODE_INVALID = "mode_invalid"
MASTER_KEY_MISSING = "master_key_missing"
MASTER_KEY_INVALID = "master_key_invalid"
MASTER_KEY_MISMATCH = "master_key_mismatch"  # 마스터 키가 틀려 DB 값을 못 푼다
KID_UNKNOWN = "kid_unknown"  # DB 값의 키 버전을 가진 마스터 키가 없다
DB_PATH_MISSING = "db_path_missing"
DB_UNAVAILABLE = "db_unavailable"
DECRYPT_FAILED = "decrypt_failed"
INVALID_TENANT = "invalid_tenant"
INVALID_NAME = "invalid_name"
INVALID_VALUE = "invalid_value"
ALREADY_EXISTS = "already_exists"
NOT_FOUND = "not_found"
REQUEST_NOT_FOUND = "request_not_found"
REQUEST_EXPIRED = "request_expired"
REQUEST_MISMATCH = "request_mismatch"
