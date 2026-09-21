from __future__ import annotations

import logging
import re

_TELEGRAM_BOT = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_QUERY_SECRET = re.compile(r"([?&](?:ms|access_token|token|key|secret)=)[^&\s\"']+", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)


def redact_secrets(text: str) -> str:
    text = _TELEGRAM_BOT.sub("bot***", text)
    text = _QUERY_SECRET.sub(r"\1***", text)
    text = _BEARER.sub(r"\1***", text)
    return text


class RedactSecretsFilter(logging.Filter):
    """Прячет токены Telegram / Метрики / Bearer из текста логов (в т.ч. URL httpx)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_secrets(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def install_secret_redaction() -> None:
    filt = RedactSecretsFilter()
    root = logging.getLogger()
    root.addFilter(filt)
    for handler in root.handlers:
        handler.addFilter(filt)
    # httpx пишет в свой логгер — дублируем на него на случай отдельного handler
    logging.getLogger("httpx").addFilter(filt)
