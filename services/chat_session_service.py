"""Chat session identifier validation and generation."""

from __future__ import annotations

import re
import uuid


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def resolve_session_id(*candidates: str | None) -> str:
    for candidate in candidates:
        if candidate and SESSION_ID_PATTERN.fullmatch(candidate):
            return candidate
    return str(uuid.uuid4())
