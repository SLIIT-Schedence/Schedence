"""Logging setup with a last-line-of-defence redaction filter.

Code in this service never logs tokens or credentials on purpose; the filter
masks anything that *looks* like a bearer token or JWT in case a library does.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_.~+/]+=*"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(aws_secret_access_key|aws_session_token|client_secret)(\s*[=:]\s*)\S+"),
)


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(r"\1[REDACTED]", text)
    text = _PATTERNS[1].sub("[REDACTED-JWT]", text)
    return _PATTERNS[2].sub(r"\1\2[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = record.getMessage()
            except Exception:
                return True
            record.args = None
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level)
    # botocore logs request details at DEBUG; never let it run below WARNING here.
    for noisy in ("botocore", "boto3", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
