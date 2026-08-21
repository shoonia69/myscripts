from __future__ import annotations

import base64
import hashlib
import hmac
import re
import unicodedata
import uuid
from dataclasses import dataclass

_ENGLISH_NAME = re.compile(r"^[A-Za-z]+(?:[ '-][A-Za-z]+)*$")
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye",
    "ґ": "g",
})


@dataclass(frozen=True)
class ClientIdentity:
    email: str
    client_id: str
    password: str
    sub_id: str
    comment: str
    telegram_id: int = 0


def normalize_name(value: str) -> str:
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 2 <= len(name) <= 64 or not _ENGLISH_NAME.fullmatch(name):
        raise ValueError("Введите имя на английском языке длиной от 2 до 64 символов.")
    return name


def _digest(name: str, secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode(), f"{purpose}:{name.casefold()}".encode(), hashlib.sha256).digest()


def _slug(name: str) -> str:
    transliterated = name.casefold().translate(_TRANSLIT)
    ascii_name = unicodedata.normalize("NFKD", transliterated).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:36] or "user"


def _build_identity(
    *,
    stable_key: str,
    secret: str,
    email_prefix: str,
    comment: str,
    telegram_id: int = 0,
) -> ClientIdentity:
    stable = _digest(stable_key, secret, "identity")
    suffix = stable.hex()[:10]
    raw_uuid = bytearray(_digest(stable_key, secret, "uuid")[:16])
    raw_uuid[6] = (raw_uuid[6] & 0x0F) | 0x50
    raw_uuid[8] = (raw_uuid[8] & 0x3F) | 0x80
    sub_id = base64.b32encode(_digest(stable_key, secret, "subscription")[:13]).decode().rstrip("=").lower()[:20]
    password = base64.urlsafe_b64encode(_digest(stable_key, secret, "password")[:18]).decode().rstrip("=")
    return ClientIdentity(
        email=f"{email_prefix}-{suffix}",
        client_id=str(uuid.UUID(bytes=bytes(raw_uuid))),
        password=password,
        sub_id=sub_id,
        comment=comment,
        telegram_id=telegram_id,
    )


def build_identity(value: str, secret: str) -> ClientIdentity:
    name = normalize_name(value)
    return _build_identity(
        stable_key=name,
        secret=secret,
        email_prefix=_slug(name),
        comment=name,
    )


def build_telegram_identity(
    value: str,
    secret: str,
    telegram_id: int,
    username: str | None,
) -> ClientIdentity:
    name = normalize_name(value)
    normalized_username = (username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", normalized_username):
        raise ValueError("Для получения подписки установите Telegram username.")
    if telegram_id <= 0:
        raise ValueError("Некорректный Telegram ID.")
    return _build_identity(
        stable_key=f"{name}|telegram:{telegram_id}",
        secret=secret,
        email_prefix=f"{_slug(name)}-tg{telegram_id}",
        comment=f"{name} | Telegram: @{normalized_username}",
        telegram_id=telegram_id,
    )
