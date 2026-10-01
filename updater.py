# -*- coding: utf-8 -*-
"""UpdateManager и NewsManager. Ничего не скачивает и не запускает — только показывает информацию."""
import re
from dataclasses import dataclass

from app_logging import get_logger
from web_api import WebError, validate_url

log = get_logger("update")


def parse_version(v):
    nums = re.findall(r"\d+", str(v))[:4]
    return tuple(int(x) for x in nums) or (0,)


@dataclass
class UpdateInfo:
    current: str
    latest: str
    changelog: str = ""
    page_url: str = ""
    source: str = ""
    available: bool = False
    error: str = ""


class UpdateManager:
    """Ожидаемый JSON: {"version":"2.1.0","changelog":"...","url":"https://..."}"""

    def __init__(self, http, current_version, update_url):
        self.http, self.current, self.url = http, current_version, update_url

    def check(self):
        info = UpdateInfo(current=self.current, latest=self.current, source=self.url or "")
        if not self.url:
            info.error = "NO_URL"
            return info
        try:
            data, meta = self.http.get_json(self.url, cache_name="update_cache.json", min_interval=0)
        except WebError as e:
            info.error = str(e)
            return info
        if not isinstance(data, dict) or "version" not in data:
            info.error = meta.get("error") or "BAD_FORMAT"
            return info
        info.latest = str(data["version"])[:16]
        info.changelog = str(data.get("changelog", ""))[:600]
        page = str(data.get("url", ""))
        try:
            info.page_url = validate_url(page) if page else ""
        except WebError:
            info.page_url = ""
        info.available = parse_version(info.latest) > parse_version(self.current)
        return info


class NewsManager:
    """Ожидаемый JSON: {"news":[{"title":"...","date":"...","text":"..."}]} или список."""

    def __init__(self, http, url, min_interval=300.0):
        self.http, self.url, self.min_interval = http, url, min_interval

    def fetch(self):
        """(список новостей, cached, error)"""
        if not self.url:
            return [], False, "NO_URL"
        try:
            data, meta = self.http.get_json(self.url, cache_name="news_cache.json", min_interval=self.min_interval)
        except WebError as e:
            return [], False, str(e)
        items = data.get("news") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return [], meta["cached"], meta.get("error") or "BAD_FORMAT"
        out = []
        for it in items[:20]:
            if isinstance(it, dict) and it.get("title"):
                out.append({"title": str(it["title"])[:80], "date": str(it.get("date", ""))[:20],
                            "text": str(it.get("text", ""))[:400]})
        return out, bool(meta["cached"]), meta.get("error") if meta["cached"] else None


def open_in_browser(url):
    """Открывает ссылку в системном браузере (только http/https)."""
    import webbrowser
    try:
        webbrowser.open(validate_url(url))
        return True
    except Exception as e:
        log.warning("open url: %s", e)
        return False
