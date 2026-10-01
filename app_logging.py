# -*- coding: utf-8 -*-
"""Логирование NeonDefender: logs/neondefender.log (с ротацией), без секретов."""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|authorization)(['\"]?\s*[:=]\s*['\"]?)([^\s'\",;&]+)")


class _RedactFilter(logging.Filter):
    """Вырезает значения ключей/токенов/паролей из сообщений."""

    def filter(self, record):
        try:
            msg = record.getMessage()
            record.msg = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***", msg)
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging(log_dir, level="INFO"):
    """Создаёт logs/neondefender.log. Никогда не бросает исключений."""
    logger = logging.getLogger("neondefender")
    if getattr(logger, "_nd_ready", False):
        return logger
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    try:
        os.makedirs(log_dir, exist_ok=True)
        h = RotatingFileHandler(os.path.join(log_dir, "neondefender.log"),
                                maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        h.addFilter(_RedactFilter())
        logger.addHandler(h)
    except Exception:
        logger.addHandler(logging.NullHandler())
    logger._nd_ready = True
    return logger


def get_logger(name):
    return logging.getLogger("neondefender." + name)
