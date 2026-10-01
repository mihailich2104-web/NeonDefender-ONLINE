# -*- coding: utf-8 -*-
"""Достижения — данные, а не код: новое достижение = одна строка в ACHIEVEMENTS."""
# id, ключ статистики, порог (>=). Названия — в locales: "ach_<id>"
ACHIEVEMENTS = [
    {"id": "first_win",   "stat": "wins",       "goal": 1},
    {"id": "kills_10",    "stat": "kills",      "goal": 10},
    {"id": "kills_100",   "stat": "kills",      "goal": 100},
    {"id": "kills_1000",  "stat": "kills",      "goal": 1000},
    {"id": "first_lan",   "stat": "lan_games",  "goal": 1},
    {"id": "no_death",    "stat": "flawless",   "goal": 1},
    {"id": "games_10",    "stat": "games",      "goal": 10},
    {"id": "games_50",    "stat": "games",      "goal": 50},
    {"id": "score_5000",  "stat": "best_score", "goal": 5000},
    {"id": "wave_10",     "stat": "best_wave",  "goal": 10},
]


def check_new(stats, unlocked):
    """Возвращает id новых разблокированных достижений."""
    return [a["id"] for a in ACHIEVEMENTS if a["id"] not in unlocked and stats.get(a["stat"], 0) >= a["goal"]]
