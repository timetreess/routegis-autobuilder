from __future__ import annotations

import logging
from pathlib import Path

from ..security import redact_sensitive_text


def _redact(message: str | None, *secrets: str) -> str | None:
    return redact_sensitive_text(message, *secrets)


class _SecretFilter(logging.Filter):
    def __init__(self, *secrets: str):
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg), *self.secrets) or ""

        def sanitized(value: object) -> object:
            rendered = str(value)
            redacted = _redact(rendered, *self.secrets) or ""
            return redacted if redacted != rendered else value

        if isinstance(record.args, tuple):
            record.args = tuple(sanitized(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: sanitized(value) for key, value in record.args.items()}
        return True


def _logger(path: Path, *secrets: str) -> logging.Logger:
    logger = logging.getLogger(f"routegis.{path.parents[1].name}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(_SecretFilter(*secrets))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _close_project_logger(project_directory: Path) -> None:
    logger = logging.getLogger(f"routegis.{project_directory.name}")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
