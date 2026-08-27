"""Redaction helpers for logs and operator-facing diagnostics."""

import logging
import re

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/\s]+):[^@\s]+@", re.I)
_SECRET_ASSIGNMENT = re.compile(r"(?i)\b(token|password|secret|discord_token)\s*[=:]\s*([^\s,;]+)")


def redact(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:***@", text)
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        elif record.args:
            record.args = tuple(redact(value) for value in record.args)
        return True
