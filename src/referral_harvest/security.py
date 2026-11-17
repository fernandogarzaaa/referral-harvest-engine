"""API-key auth (bcrypt), admin cookie sessions, and CSRF tokens."""

import secrets

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from referral_harvest.config import get_settings
from referral_harvest.models import ApiKey

API_KEY_PREFIX = "rh_"
_API_KEY_BYTES = 32


def make_api_key(tenant_id: str, name: str = "default") -> tuple[str, ApiKey]:
    """Generate a raw API key and the DB record holding its bcrypt hash.

    The raw key is shown exactly once; only the hash is stored.
    """
    raw = API_KEY_PREFIX + secrets.token_urlsafe(_API_KEY_BYTES)
    rounds = get_settings().bcrypt_rounds
    key_hash = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=rounds)).decode()
    record = ApiKey(
        tenant_id=tenant_id,
        prefix=raw[:12],
        key_hash=key_hash,
        name=name,
    )
    return raw, record


def verify_api_key(raw: str, record: ApiKey) -> bool:
    if record.revoked_at is not None:
        return False
    return bcrypt.checkpw(raw.encode(), record.key_hash.encode())


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().admin_token, salt="referral-harvest-admin")


def make_admin_cookie() -> str:
    return _serializer().dumps({"admin": True})


def verify_admin_cookie(value: str) -> bool:
    try:
        data = _serializer().loads(value)
    except (BadSignature, SignatureExpired):
        return False
    return data.get("admin") is True


def make_csrf_token() -> str:
    return URLSafeTimedSerializer(get_settings().admin_token, salt="csrf").dumps(
        {"csrf": secrets.token_hex(8)}
    )


def verify_csrf_token(token: str, max_age: int = 3600) -> bool:
    try:
        URLSafeTimedSerializer(get_settings().admin_token, salt="csrf").loads(
            token, max_age=max_age
        )
    except (BadSignature, SignatureExpired):
        return False
    return True
