"""Server-only identity, separate HKDF domains, authenticated pause credentials."""
from __future__ import annotations
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from uuid import UUID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from .errors import WebApplicationConfigurationError, WebApplicationUsageError
from .models import utc

KEY_VERSION = "cta-pause-key-v1"

def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

def decode(value: str) -> bytes:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise WebApplicationUsageError()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if encode(raw) != value:
            raise ValueError()
        return raw
    except Exception:
        raise WebApplicationUsageError() from None

def load_pause_key(*, path: Path) -> bytes:
    try:
        if type(path) is not type(Path()) or not path.is_absolute() or path.stat().st_size > 1024:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if type(data) is not dict or set(data) != {"version", "key"} or data["version"] != KEY_VERSION:
            raise ValueError()
        key = base64.b64decode(data["key"], validate=True)
        if len(key) != 32 or base64.b64encode(key).decode("ascii") != data["key"]:
            raise ValueError()
        return key
    except Exception:
        raise WebApplicationConfigurationError() from None

def cookie_hash(cookie: str) -> str:
    if len(cookie) != 43 or len(decode(cookie)) != 32:
        raise WebApplicationUsageError()
    return hashlib.sha256(cookie.encode("ascii")).hexdigest()

def new_cookie() -> str:
    return encode(secrets.token_bytes(32))

def ip_hash(client_ip: str) -> str:
    try:
        ip = ipaddress.ip_address(client_ip)
        if getattr(ip, "scope_id", None) is not None:
            raise ValueError()
        return hashlib.sha256(ip.compressed.encode("ascii")).hexdigest()
    except Exception:
        raise WebApplicationUsageError() from None

def derive(key: bytes, domain: str) -> bytes:
    if type(key) is not bytes or len(key) != 32 or domain not in {"csrf-v1", "request-v1"}:
        raise WebApplicationUsageError()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"cta-web-v1", info=domain.encode()).derive(key)

def csrf_token(key: bytes, visitor_hash: str) -> str:
    return encode(hmac.digest(derive(key, "csrf-v1"), visitor_hash.encode("ascii"), "sha256"))

def check_csrf(key: bytes, visitor_hash: str, supplied: str) -> bool:
    return type(supplied) is str and supplied.isascii() and len(supplied) <= 128 and hmac.compare_digest(
        csrf_token(key, visitor_hash), supplied)

def body_fingerprint(key: bytes, *, route: str, run_id: UUID | None, generation: int | None, body) -> str:
    return hmac.digest(derive(key, "request-v1"), canonical_json(dict(
        route=route, run_id=str(run_id) if run_id else None, pause_generation=generation, body=body)), "sha256").hex()

def pause_aad(*, run_id: UUID, pause_generation: int, contract_digest: str) -> bytes:
    if (type(run_id) is not UUID or type(pause_generation) is not int or not 0 <= pause_generation <= 2**53-1 or
            type(contract_digest) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", contract_digest) is None):
        raise WebApplicationUsageError()
    return f"cta-pause-v1|{run_id}|{pause_generation}|{contract_digest}".encode("utf-8")

def encrypt_pause(key: bytes, *, run_id: UUID, pause_generation: int, contract_digest: str, pause_token: str):
    if type(key) is not bytes or len(key) != 32 or type(pause_token) is not str or not pause_token:
        raise WebApplicationUsageError()
    aad = pause_aad(run_id=run_id, pause_generation=pause_generation, contract_digest=contract_digest)
    nonce = secrets.token_bytes(12)
    return nonce, AESGCM(key).encrypt(nonce, pause_token.encode("utf-8"), aad)

def decrypt_pause(key: bytes, *, run_id: UUID, pause_generation: int, contract_digest: str, nonce: bytes, ciphertext: bytes) -> str:
    try:
        aad = pause_aad(run_id=run_id, pause_generation=pause_generation, contract_digest=contract_digest)
        if type(key) is not bytes or len(key) != 32 or type(nonce) is not bytes or len(nonce) != 12 or type(ciphertext) is not bytes or len(ciphertext) < 16:
            raise ValueError()
        value = AESGCM(key).decrypt(nonce, ciphertext, aad).decode("utf-8")
        if not value:
            raise ValueError()
        return value
    except Exception:
        raise WebApplicationUsageError() from None

def make_cursor(key: bytes, *, visitor_hash: str, bound_at: datetime, run_id: UUID) -> str:
    data = canonical_json(dict(visitor_hash=visitor_hash, bound_at=utc(bound_at).isoformat(), run_id=str(run_id)))
    return encode(data) + "." + encode(hmac.digest(derive(key, "request-v1"), b"cursor-v1|" + data, "sha256"))

def read_cursor(key: bytes, *, cursor: str, visitor_hash: str) -> tuple[datetime, UUID]:
    try:
        if type(cursor) is not str or not 1 <= len(cursor) <= 512:
            raise ValueError()
        data_part, tag_part = cursor.split(".")
        data, tag = decode(data_part), decode(tag_part)
        if not hmac.compare_digest(tag, hmac.digest(derive(key, "request-v1"), b"cursor-v1|" + data, "sha256")):
            raise ValueError()
        value = json.loads(data)
        if set(value) != {"visitor_hash", "bound_at", "run_id"} or value["visitor_hash"] != visitor_hash:
            raise ValueError()
        when, run_id = utc(datetime.fromisoformat(value["bound_at"])), UUID(value["run_id"])
        if value["run_id"] != str(run_id):
            raise ValueError()
        return when, run_id
    except Exception:
        raise WebApplicationUsageError() from None

class EncryptedPauseCredentialSink:
    def __init__(self, *, store):
        self._store = store
    async def store(self, *, run_id: UUID, pause_generation: int, contract_digest: str, pause_token: str) -> None:
        await self._store.save_pause(run_id=run_id, pause_generation=pause_generation,
            contract_digest=contract_digest, pause_token=pause_token)
