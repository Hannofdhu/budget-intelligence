import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        raise TypeError("binary floating point values are not allowed in canonical content")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return value


def content_hash(value: Any) -> str:
    encoded = json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
