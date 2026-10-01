# -*- coding: utf-8 -*-
"""NotificationManager: единые уведомления INFO/SUCCESS/WARNING/ERROR."""
import time
from collections import deque

INFO, SUCCESS, WARNING, ERROR = "INFO", "SUCCESS", "WARNING", "ERROR"
ICONS = {INFO: "i", SUCCESS: "+", WARNING: "!", ERROR: "x"}
COLORS = {INFO: (80, 200, 255), SUCCESS: (80, 255, 140), WARNING: (255, 220, 80), ERROR: (255, 90, 90)}


class NotificationManager:
    def __init__(self, max_visible=4, history=50, ttl=4.0):
        self.ttl = ttl
        self.max_visible = max_visible
        self.items = deque(maxlen=max_visible)      # активные
        self.history = deque(maxlen=history)        # ограниченная история

    def push(self, kind, text, ttl=None):
        kind = kind if kind in ICONS else INFO
        n = {"kind": kind, "text": str(text)[:120], "t": time.monotonic(), "ttl": ttl or self.ttl}
        if self.items and self.items[-1]["text"] == n["text"]:
            self.items[-1]["t"] = n["t"]           # не дублируем одинаковые
            return
        self.items.append(n)
        self.history.append(n)

    def info(self, t): self.push(INFO, t)
    def success(self, t): self.push(SUCCESS, t)
    def warning(self, t): self.push(WARNING, t)
    def error(self, t): self.push(ERROR, t, ttl=6.0)

    def active(self):
        now = time.monotonic()
        while self.items and now - self.items[0]["t"] > self.items[0]["ttl"]:
            self.items.popleft()
        return list(self.items)

    def draw(self, surf, font, width):
        """Рисует уведомления в правом верхнем углу (текст + иконка, не только цвет)."""
        import pygame
        y = 10
        for n in self.active():
            label = "%s %s" % (ICONS[n["kind"]], n["text"])
            txt = font.render(label, True, (255, 255, 255))
            box = txt.get_rect().inflate(20, 12)
            box.topright = (width - 10, y)
            pygame.draw.rect(surf, (15, 18, 30), box, border_radius=6)
            pygame.draw.rect(surf, COLORS[n["kind"]], box, width=2, border_radius=6)
            surf.blit(txt, txt.get_rect(center=box.center))
            y += box.height + 6
