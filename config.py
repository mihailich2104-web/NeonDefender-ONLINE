# -*- coding: utf-8 -*-
"""ConfigManager: config.json с миграцией версий, бэкапом и атомарной записью."""
import copy
import json
import os
import time

from app_logging import get_logger

log = get_logger("config")
CONFIG_VERSION = 2

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "nickname": "Player",
    "volume": 1.0, "music_volume": 0.7, "effects_volume": 1.0,
    "resolution": [1024, 640], "fullscreen": False, "fps": 60, "vsync": True,
    "quality": "low", "language": "ru", "sensitivity": 1.0,
    "server_ip": "127.0.0.1", "server_port": 5000,
    "last_server": "", "selected_map": "default", "selected_mode": "classic",
    "net_timeout": 5.0, "auto_reconnect": True,
    "update_check": True, "news_enabled": True, "web_servers_enabled": True,
    "update_url": "", "news_url": "",
    "website_url": "", "discord_url": "", "docs_url": "", "servers_page_url": "", "updates_page_url": "",
    # [{"name":..., "url":..., "enabled":True, "timeout":5, "api_key_env":"", "format":"standard"}]
    "web_services": [],
    # Центральный каталог публичных серверов. Пусто = только LAN/избранные/недавние.
    "online_master_url": "",
    "online_servers": [],
    "online_master_enabled": True,
    "public_server_host": "",
    "public_server_name": "Neon Defender",
    # минимальные интервалы между запросами, сек
    "rate_servers": 5.0, "rate_news": 300.0,
}


def atomic_write_json(path, data, keep_backup=True):
    """Запись через временный файл + os.replace; прежняя версия -> path.bak."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    if keep_backup and os.path.isfile(path):
        try:
            with open(path, "rb") as src, open(path + ".bak", "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    os.replace(tmp, path)


def safe_read_json(path, default=None):
    """Читает JSON; при порче пробует path.bak; иначе default. Битый файл -> .corrupt-<ts>."""
    for p in (path, path + ".bak"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("Повреждённый JSON %s: %s", p, e)
            if p == path:
                try:
                    os.replace(p, "%s.corrupt-%d" % (p, int(time.time())))
                except OSError:
                    pass
    return copy.deepcopy(default)


def upgrade_config(cfg):
    """Миграция старых конфигов к текущей версии (недостающие поля — по умолчанию)."""
    if not isinstance(cfg, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    ver = cfg.get("config_version", 1)
    if not isinstance(ver, int):
        ver = 1
    if ver < 2:
        # v1 -> v2: старое поле "lang" стало "language", "name" -> "nickname"
        if "lang" in cfg and "language" not in cfg:
            cfg["language"] = cfg.pop("lang")
        if "name" in cfg and "nickname" not in cfg:
            cfg["nickname"] = cfg.pop("name")
    out = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in cfg.items():
        out[k] = v
    out["config_version"] = CONFIG_VERSION
    return _sanitize(out)


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _sanitize(c):
    d = DEFAULT_CONFIG
    for k in ("volume", "music_volume", "effects_volume"):
        c[k] = _num(c[k], 0.0, 1.0, d[k])
    c["sensitivity"] = _num(c["sensitivity"], 0.2, 3.0, 1.0)
    c["net_timeout"] = _num(c["net_timeout"], 1.0, 30.0, 5.0)
    c["fps"] = int(_num(c["fps"], 20, 240, 60))
    c["server_port"] = int(_num(c["server_port"], 1, 65535, 5000))
    c["rate_servers"] = _num(c["rate_servers"], 1.0, 3600.0, 5.0)
    c["rate_news"] = _num(c["rate_news"], 30.0, 86400.0, 300.0)
    nick = str(c.get("nickname") or "Player").strip()[:16]
    c["nickname"] = nick or "Player"
    if not isinstance(c.get("online_servers"), list):
        c["online_servers"] = []
    c["online_servers"] = [s for s in c["online_servers"] if isinstance(s, dict)][:200]
    if not isinstance(c.get("web_services"), list):
        c["web_services"] = []
    c["web_services"] = [s for s in c["web_services"] if isinstance(s, dict) and s.get("url")][:20]
    for k in ("fullscreen", "vsync", "auto_reconnect", "update_check", "news_enabled", "web_servers_enabled", "online_master_enabled"):
        c[k] = bool(c[k])
    return c


class ConfigManager:
    def __init__(self, directory):
        self.dir = directory
        self.path = os.path.join(directory, "config.json")
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self._dirty = False
        self.load()

    def load(self):
        raw = safe_read_json(self.path, None)
        existed = raw is not None
        self.data = upgrade_config(raw) if existed else copy.deepcopy(DEFAULT_CONFIG)
        if not existed or raw.get("config_version") != CONFIG_VERSION:
            self._dirty = True
            self.save()
        return self.data

    def get(self, key, default=None):
        return self.data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key, value):
        if self.data.get(key) != value:
            self.data[key] = value
            self._dirty = True

    def save(self, force=False):
        """Пишет на диск только если что-то менялось (не перезаписываем JSON постоянно)."""
        if not (self._dirty or force):
            return True
        try:
            atomic_write_json(self.path, self.data)
            self._dirty = False
            return True
        except Exception as e:
            log.error("Не удалось сохранить config: %s", e)
            return False
