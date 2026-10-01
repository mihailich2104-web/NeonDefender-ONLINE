# -*- coding: utf-8 -*-
"""HTTP-клиент: timeout, retry, кэш, rate limit, проверка URL. Только stdlib.
Запросы выполняются в фоновых потоках (BackgroundFetcher) — игровой цикл не блокируется."""
import ipaddress
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from app_logging import get_logger
from config import atomic_write_json, safe_read_json

log = get_logger("web")
USER_AGENT = "NeonDefender/2.1 (+game client)"
MAX_BYTES = 1024 * 1024  # ограничение размера ответа


class WebError(Exception):
    pass


def validate_url(url, allow_private=True):
    """Только http(s), без логина/пароля в URL. Возвращает нормализованный URL или бросает WebError."""
    try:
        p = urlparse(str(url).strip())
    except Exception:
        raise WebError("BAD_URL")
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise WebError("BAD_URL")
    if not allow_private:
        try:
            ip = ipaddress.ip_address(p.hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise WebError("PRIVATE_URL")
        except ValueError:
            pass
    return p.geturl()


class HttpClient:
    def __init__(self, cache_dir=None, timeout=5.0, retries=2, min_interval=2.0):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.retries = max(0, min(retries, 3))   # бесконечных retry нет
        self.min_interval = min_interval
        self._last = {}
        self._lock = threading.Lock()

    def _cache_path(self, name):
        return os.path.join(self.cache_dir, name) if self.cache_dir else None

    def get_json(self, url, headers=None, timeout=None, min_interval=None, cache_name=None):
        """Возвращает (data, meta). meta = {"cached": bool, "error": str|None, "ts": float}.
        Не бросает исключений: при ошибке отдаёт кэш (cached=True) либо (None, meta с error)."""
        url = validate_url(url)
        interval = self.min_interval if min_interval is None else min_interval
        cpath = self._cache_path(cache_name) if cache_name else None
        now = time.time()
        with self._lock:
            last = self._last.get(url, 0)
            throttled = now - last < interval
            if not throttled:
                self._last[url] = now
        if throttled:
            return self._from_cache(cpath, url, "RATE_LIMITED")
        h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        h.update(headers or {})
        err = "UNKNOWN"
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers=h)
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    raw = r.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise WebError("TOO_LARGE")
                data = json.loads(raw.decode("utf-8"))
                if cpath:
                    try:
                        atomic_write_json(cpath, {"url": url, "ts": time.time(), "data": data}, keep_backup=False)
                    except Exception as e:
                        log.warning("cache write: %s", e)
                return data, {"cached": False, "error": None, "ts": time.time()}
            except urllib.error.HTTPError as e:
                err = "HTTP_%d" % e.code
                if e.code < 500:
                    break            # 4xx повторять бессмысленно
            except json.JSONDecodeError:
                err = "BAD_JSON"
                break
            except WebError as e:
                err = str(e)
                break
            except Exception as e:   # DNS, timeout, offline...
                err = type(e).__name__
            time.sleep(0.4 * (attempt + 1))
        log.warning("GET %s failed: %s", _safe_url(url), err)
        return self._from_cache(cpath, url, err)

    def _from_cache(self, cpath, url, err):
        if cpath:
            c = safe_read_json(cpath, None)
            if isinstance(c, dict) and c.get("url") == url and "data" in c:
                return c["data"], {"cached": True, "error": err, "ts": c.get("ts", 0)}
        return None, {"cached": False, "error": err, "ts": 0}


def _safe_url(url):
    p = urlparse(url)
    return "%s://%s%s" % (p.scheme, p.hostname, p.path)   # без query (там могут быть ключи)


class BackgroundFetcher:
    """Запускает функцию в daemon-потоке; результат забирается из главного цикла через poll().
    Одновременно не более одной задачи на имя — потоки не копятся."""

    def __init__(self):
        self._results = {}
        self._running = set()
        self._lock = threading.Lock()

    def submit(self, name, fn, *args):
        with self._lock:
            if name in self._running:
                return False
            self._running.add(name)

        def work():
            try:
                res = fn(*args)
            except Exception as e:
                log.error("task %s: %s", name, e)
                res = None
            with self._lock:
                self._results[name] = res
                self._running.discard(name)
        threading.Thread(target=work, daemon=True, name="nd-" + name).start()
        return True

    def busy(self, name):
        with self._lock:
            return name in self._running

    def poll(self, name):
        """Возвращает (ready, result) и забирает результат."""
        with self._lock:
            if name in self._results:
                return True, self._results.pop(name)
        return False, None
