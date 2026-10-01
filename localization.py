# -*- coding: utf-8 -*-
"""Переводы новых экранов: locales/<lang>.json, запасной язык — английский.
Старые строки игры остаются в TEXTS внутри game.py и по-прежнему имеют приоритет."""
import json
import os
import sys

from app_logging import get_logger

log = get_logger("i18n")
_cache = {}


def _locale_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "locales")


def _load(lang):
    if lang not in _cache:
        try:
            with open(os.path.join(_locale_dir(), lang + ".json"), "r", encoding="utf-8") as f:
                d = json.load(f)
            _cache[lang] = d if isinstance(d, dict) else {}
        except Exception as e:
            if lang != "en":
                log.warning("locale %s: %s", lang, e)
            _cache[lang] = {}
    return _cache[lang]


def lookup(lang, key):
    """Строка или None, если нет ни в языке, ни в английском."""
    v = _load(lang).get(key)
    if v is None:
        v = _load("en").get(key)
    return v


def tr(lang, key, *args):
    s = lookup(lang, key)
    if s is None:
        return key
    if args:
        try:
            return s.format(*args)
        except Exception:
            return s
    return s
