"""암호화: AES-256-GCM, 레코드마다 새 nonce, AAD에 tenant·name·kid, 마스터 키에서 HKDF로 파생.

- 마스터 키: `AX_SECRET_MASTER_KEY`(base64 32바이트 이상),
  키 버전 `AX_SECRET_MASTER_KEY_ID`(기본 `k1`).
  교체 대비로 옛 키는 `AX_SECRET_MASTER_KEYS_OLD="kid=base64,kid=base64"`에 남긴다(복호화 전용).
- 마스터 키를 그대로 쓰지 않는다. 용도별(`encrypt`, `fingerprint`) 파생 키를 쓴다.
- 예외는 사유 코드(`SecretStoreError`)만 담는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ax_secret_store import errors
from ax_secret_store.errors import SecretStoreError

MASTER_KEY_ENV = "AX_SECRET_MASTER_KEY"
MASTER_KEY_ID_ENV = "AX_SECRET_MASTER_KEY_ID"
OLD_KEYS_ENV = "AX_SECRET_MASTER_KEYS_OLD"
DEFAULT_KID = "k1"
MIN_KEY_BYTES = 32
NONCE_BYTES = 12
KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_INFO = b"ax-secret-store/v1/"


def generate_master_key() -> str:
    """새 마스터 키(base64 32바이트). 설치할 때 한 번 만들어 `.env`에 넣고 따로 백업한다."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _decode_key(raw: str) -> bytes:
    text = raw.strip()
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise SecretStoreError(errors.MASTER_KEY_INVALID) from None
    if len(data) < MIN_KEY_BYTES:
        raise SecretStoreError(errors.MASTER_KEY_INVALID)
    return data


def _aad(tenant: str, name: str, kid: str) -> bytes:
    return f"ax-secret/v1|{tenant}|{name}|{kid}".encode()


@dataclass(frozen=True)
class Keyring:
    """현재 키와 옛 키(복호화 전용). repr·str에 키가 나오지 않는다."""

    current_kid: str
    _masters: dict[str, bytes] = field(repr=False)

    @classmethod
    def from_env(cls) -> Keyring:
        raw = os.getenv(MASTER_KEY_ENV, "").strip()
        if not raw:
            raise SecretStoreError(errors.MASTER_KEY_MISSING)
        kid = os.getenv(MASTER_KEY_ID_ENV, "").strip() or DEFAULT_KID
        if not KID_RE.match(kid):
            raise SecretStoreError(errors.MASTER_KEY_INVALID)
        masters = {kid: _decode_key(raw)}
        for item in os.getenv(OLD_KEYS_ENV, "").split(","):
            old_kid, sep, old_raw = item.strip().partition("=")
            if sep and old_kid and old_kid != kid:
                if not KID_RE.match(old_kid):
                    raise SecretStoreError(errors.MASTER_KEY_INVALID)
                masters[old_kid] = _decode_key(old_raw)
        return cls(kid, masters)

    def has(self, kid: str) -> bool:
        return kid in self._masters

    def _derive(self, kid: str, purpose: str) -> bytes:
        master = self._masters.get(kid)
        if master is None:
            raise SecretStoreError(errors.KID_UNKNOWN)
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + purpose.encode()
        ).derive(master)

    def encrypt(self, tenant: str, name: str, plaintext: str) -> tuple[str, bytes, bytes]:
        """(kid, nonce, 암호문+태그). 현재 키 버전으로 암호화한다."""
        kid = self.current_kid
        nonce = os.urandom(NONCE_BYTES)
        key = self._derive(kid, "encrypt")
        blob = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(tenant, name, kid))
        return kid, nonce, blob

    def decrypt(self, tenant: str, name: str, kid: str, nonce: bytes, blob: bytes) -> str:
        key = self._derive(kid, "encrypt")  # kid를 모르면 kid_unknown
        try:
            raw = AESGCM(key).decrypt(nonce, blob, _aad(tenant, name, kid))
            return raw.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise SecretStoreError(errors.DECRYPT_FAILED) from None

    def fingerprint(self, tenant: str, name: str, value: str) -> str:
        """값을 되짚을 수 없는 일치 확인용 지문(HMAC-SHA256 앞 16자). 현재 마스터 키에서만 같다."""
        key = self._derive(self.current_kid, "fingerprint")
        mac = hmac.new(key, f"{tenant}|{name}|".encode() + value.encode("utf-8"), hashlib.sha256)
        return mac.hexdigest()[:16]
