# -*- coding: utf-8 -*-
"""Поиск серверов: ServerInfo, провайдеры (LAN/Local/Custom/Official), избранное, недавние."""
import os
import socket
import time
import zlib
from dataclasses import dataclass, asdict

from app_logging import get_logger
from config import atomic_write_json, safe_read_json
from web_api import HttpClient, WebError, validate_url

log = get_logger("servers")


@dataclass
class ServerInfo:
    name: str = "Server"
    address: str = ""
    port: int = 5000
    players: int = 0
    max_players: int = 2
    ping: int = -1            # -1 = неизвестно
    version: str = ""
    mode: str = ""
    map: str = ""
    description: str = ""
    country: str = ""
    online: bool = True
    source: str = "LAN"
    cached: bool = False

    def key(self):
        return "%s:%s" % (self.address, self.port)

    def ping_text(self):
        return "Ping: %d ms" % self.ping if self.ping >= 0 else "Ping: —"


def parse_server(d, source):
    """Стандартный JSON-объект -> ServerInfo. Неверные данные -> None (сервер пропускается)."""
    if not isinstance(d, dict):
        return None
    addr = str(d.get("address") or d.get("ip") or d.get("host") or "").strip()
    try:
        port = int(d.get("port", 5000))
    except (TypeError, ValueError):
        return None
    if not addr or len(addr) > 255 or not (1 <= port <= 65535) or any(c in addr for c in " /\\\"'<>"):
        return None

    def i(k, dflt):
        try:
            return max(0, int(d.get(k, dflt)))
        except (TypeError, ValueError):
            return dflt

    return ServerInfo(name=str(d.get("name") or addr)[:40], address=addr, port=port,
                      players=i("players", 0), max_players=i("max_players", 2),
                      ping=-1, version=str(d.get("version", ""))[:16], mode=str(d.get("mode", ""))[:16],
                      map=str(d.get("map", ""))[:24], description=str(d.get("description", ""))[:120],
                      country=str(d.get("country", ""))[:3], online=bool(d.get("online", True)), source=source)


def standard_adapter(data, source):
    """Стандартный формат: {"servers":[...]} или просто [...]. Для другого формата — свой адаптер."""
    items = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:200]:
        s = parse_server(it, source)
        if s:
            out.append(s)
    return out


ADAPTERS = {"standard": standard_adapter}


def measure_ping(host, port, timeout=1.0):
    """TCP-ping (время установки соединения), мс. -1 если недоступен."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
    except Exception:
        return -1
    return int((time.perf_counter() - t0) * 1000)


class StaticOnlineProvider(BaseProvider):
    """Список интернет-серверов прямо из config.json. Полезен без отдельного master-сервера."""
    name = "ONLINE"

    def __init__(self, entries):
        self.entries = entries if isinstance(entries, list) else []

    def get_servers(self):
        out = []
        for item in self.entries[:200]:
            sv = parse_server(item, "ONLINE")
            if sv:
                out.append(sv)
        return out, None


class BaseProvider:
    name = "base"

    def get_servers(self):
        return [], None          # (список ServerInfo, ошибка|None)

    def get_news(self):
        return [], None

    def get_status(self):
        return {"name": self.name, "available": True}


class LANProvider(BaseProvider):
    """Обёртка над существующим network.LobbyBrowser (UDP-обнаружение). Сам browser живёт в игре."""
    name = "LAN"

    def __init__(self, browser_getter, version=""):
        self._get = browser_getter
        self.version = version

    def get_servers(self):
        b = self._get()
        out = []
        for g in (b.list_games() if b else []):
            out.append(ServerInfo(name=str(g.get("name", "LAN")), address=g.get("ip", ""), port=int(g.get("port", 5000)),
                                  players=int(g.get("players", 0)), max_players=int(g.get("max_players", 2)),
                                  version=self.version, source="LAN", mode="coop"))
        return out, None


class LocalProvider(BaseProvider):
    """Проверка локального сервера на этом компьютере (127.0.0.1:port)."""
    name = "LOCAL"

    def __init__(self, port_getter):
        self._port = port_getter

    def get_servers(self):
        port = self._port()
        ping = measure_ping("127.0.0.1", port, 0.3)
        if ping < 0:
            return [], None
        return [ServerInfo(name="Localhost", address="127.0.0.1", port=port, ping=ping, source="LOCAL")], None


class CustomAPIProvider(BaseProvider):
    """Внешний сервис из config["web_services"]. Один неработающий источник не ломает остальные."""

    def __init__(self, svc, http, kind="WEB"):
        self.svc = svc
        self.http = http
        self.name = str(svc.get("name", "Web"))[:24]
        self.kind = kind
        self._status = {"name": self.name, "available": None}

    def _headers(self):
        env = self.svc.get("api_key_env")             # ключ берём из переменной окружения, не из кода
        key = os.environ.get(env, "") if env else ""
        return {"Authorization": "Bearer " + key} if key else {}

    def get_servers(self):
        url = self.svc.get("api_url") or self.svc.get("url")
        try:
            data, meta = self.http.get_json(url, headers=self._headers(), timeout=float(self.svc.get("timeout", 5)),
                                            cache_name="server_cache_%08x.json" % zlib.crc32(str(url).encode()))
        except WebError as e:
            return [], str(e)
        self._status = {"name": self.name, "available": data is not None}
        if data is None:
            return [], meta["error"]
        adapter = ADAPTERS.get(self.svc.get("format", "standard"), standard_adapter)
        try:
            servers = adapter(data, self.name)
        except Exception as e:
            log.warning("adapter %s: %s", self.name, e)
            return [], "BAD_FORMAT"
        for s in servers:
            s.cached = bool(meta["cached"])
        return servers, ("CACHED" if meta["cached"] else None)

    def get_status(self):
        return self._status


class OfficialAPIProvider(CustomAPIProvider):
    """Публичный каталог Neon Defender. Формат тот же, что у обычного API."""
    pass


class OnlineMasterProvider(CustomAPIProvider):
    """Интернет-каталог серверов. Не использует LAN broadcast: работает через HTTP(S)."""
    def __init__(self, url, http):
        svc = {"name": "ONLINE", "url": url, "format": "standard", "enabled": True}
        super().__init__(svc, http, kind="ONLINE")
        self.name = "ONLINE"


class ServerStore:
    """Избранное / недавние: локальные JSON с атомарной записью. Размер ограничен."""

    def __init__(self, directory, name, limit):
        self.path = os.path.join(directory, name)
        self.limit = limit
        data = safe_read_json(self.path, [])
        self.items = [x for x in data if isinstance(x, dict) and x.get("address")][:limit] if isinstance(data, list) else []

    def _save(self):
        try:
            atomic_write_json(self.path, self.items)
        except Exception as e:
            log.error("store save: %s", e)

    def has(self, address, port):
        return any(x["address"] == address and int(x.get("port", 0)) == int(port) for x in self.items)

    def add(self, address, port, name=""):
        port = int(port)
        self.items = [x for x in self.items if not (x["address"] == address and int(x.get("port", 0)) == port)]
        self.items.insert(0, {"name": name or "%s:%d" % (address, port), "address": address, "port": port})
        del self.items[self.limit:]
        self._save()

    def remove(self, address, port):
        self.items = [x for x in self.items if not (x["address"] == address and int(x.get("port", 0)) == int(port))]
        self._save()

    def rename(self, address, port, name):
        for x in self.items:
            if x["address"] == address and int(x.get("port", 0)) == int(port):
                x["name"] = name[:40]
        self._save()

    def clear(self):
        self.items = []
        self._save()


class ServerDiscovery:
    """Единый список: LAN + LOCAL + WEB + FAVORITES + RECENT. Сетевая часть вызывается из фонового потока."""

    def __init__(self, providers, favorites, recent):
        self.providers = providers
        self.favorites = favorites
        self.recent = recent

    def refresh(self):
        """Возвращает (список ServerInfo, {источник: ошибка})."""
        result, errors, seen = [], {}, set()
        for p in self.providers:
            try:
                servers, err = p.get_servers()
            except Exception as e:
                log.error("provider %s: %s", p.name, e)
                servers, err = [], "ERROR"
            if err and err != "CACHED":
                errors[p.name] = err
            elif err == "CACHED":
                errors[p.name] = "CACHED"
            for s in servers:
                if s.key() not in seen:
                    seen.add(s.key())
                    result.append(s)
        for store, label in ((self.favorites, "FAV"), (self.recent, "RECENT")):
            for x in store.items:
                k = "%s:%s" % (x["address"], x["port"])
                if k in seen:
                    continue
                seen.add(k)
                result.append(ServerInfo(name=x.get("name", k), address=x["address"], port=int(x["port"]), source=label))
        # ping по всем (быстро, параллельно)
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            pings = list(ex.map(lambda s: measure_ping(s.address, s.port, 0.8), result[:40]))
        for s, p in zip(result, pings):
            s.ping = p
            if p < 0 and s.source in ("FAV", "RECENT"):
                s.online = False
        return result, errors
