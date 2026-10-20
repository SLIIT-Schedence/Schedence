"""Shared deterministic JSON; independent of persistence and solver modules."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from uuid import UUID
import json
import math
from pydantic import BaseModel

def _json_value(value):
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=canonical_json)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value) -> str:
    """Stable unordered sets/maps; preserve source text and ordered arrays exactly."""
    return json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def content_digest(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


