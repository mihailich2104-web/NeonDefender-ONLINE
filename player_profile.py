# -*- coding: utf-8 -*-
"""Локальный профиль игрока: уровень, опыт, статистика, достижения. Работает offline."""
import os

from app_logging import get_logger
from achievements import check_new
from config import atomic_write_json, safe_read_json

log = get_logger("profile")

DEFAULT_STATS = {"kills": 0, "deaths": 0, "wins": 0, "losses": 0, "games": 0, "lan_games": 0,
                 "play_time": 0.0, "best_score": 0, "best_wave": 0, "flawless": 0}


def xp_for_level(level):
    return 200 + 100 * (level - 1)


class PlayerProfile:
    def __init__(self, directory):
        self.path = os.path.join(directory, "profile.json")
        raw = safe_read_json(self.path, {})
        raw = raw if isinstance(raw, dict) else {}
        self.nickname = str(raw.get("nickname", "Player"))[:16]
        self.level = max(1, int(raw.get("level", 1) or 1))
        self.xp = max(0, int(raw.get("xp", 0) or 0))
        self.stats = dict(DEFAULT_STATS)
        s = raw.get("stats", {})
        if isinstance(s, dict):
            for k in DEFAULT_STATS:
                v = s.get(k, DEFAULT_STATS[k])
                if isinstance(v, (int, float)) and v >= 0:
                    self.stats[k] = v
        a = raw.get("achievements", [])
        self.unlocked = [x for x in a if isinstance(x, str)] if isinstance(a, list) else []

    def save(self):
        try:
            atomic_write_json(self.path, {"nickname": self.nickname, "level": self.level, "xp": self.xp,
                                          "stats": self.stats, "achievements": self.unlocked})
        except Exception as e:
            log.error("profile save: %s", e)

    def record_match(self, score, kills, deaths, seconds, wave, won, lan=False):
        """Вызывается один раз по окончании матча. Возвращает список новых достижений."""
        st = self.stats
        st["games"] += 1
        st["kills"] += max(0, kills)
        st["deaths"] += max(0, deaths)
        st["play_time"] += max(0.0, seconds)
        st["best_score"] = max(st["best_score"], score)
        st["best_wave"] = max(st["best_wave"], wave)
        if won:
            st["wins"] += 1
        else:
            st["losses"] += 1
        if lan:
            st["lan_games"] += 1
        if deaths == 0 and kills > 0 and seconds >= 30:
            st["flawless"] += 1
        self.xp += score // 10 + kills
        while self.xp >= xp_for_level(self.level):
            self.xp -= xp_for_level(self.level)
            self.level += 1
        new = check_new(st, self.unlocked)
        self.unlocked.extend(new)
        self.save()
        return new

    def kd(self):
        return self.stats["kills"] / max(1, self.stats["deaths"])
