# -*- coding: utf-8 -*-
"""Новые экраны NeonDefender в виде mixin для класса App (game.py).
Здесь: профиль/статистика, новости и обновления, браузер серверов, сетевые настройки,
экран потери соединения, уведомления. Старый код игры не затрагивается."""
import ipaddress
import threading
import time

import pygame

import localization
from achievements import ACHIEVEMENTS
from app_logging import get_logger
from config import ConfigManager
from notifications import NotificationManager
from player_profile import PlayerProfile, xp_for_level
from server_browser import (CustomAPIProvider, LANProvider, LocalProvider, OfficialAPIProvider,
                            OnlineMasterProvider, StaticOnlineProvider, ServerDiscovery, ServerStore)
from updater import NewsManager, UpdateManager, open_in_browser
from web_api import BackgroundFetcher, HttpClient

log = get_logger("ui")

CYAN, CYAN_SOFT = (70, 220, 255), (110, 235, 255)
YELLOW, GREEN, RED_SOFT = (255, 220, 90), (110, 255, 150), (255, 130, 110)
WHITE, GRAY, DARK = (240, 245, 255), (150, 160, 190), (60, 66, 90)

LOGICAL_W, LOGICAL_H = 1024, 640
LINKS = [("link_website", "website_url"), ("link_discord", "discord_url"), ("link_docs", "docs_url"),
         ("link_servers", "servers_page_url"), ("link_updates", "updates_page_url")]


def fmt_time(sec):
    sec = int(sec)
    return "%dh %02dm" % (sec // 3600, sec % 3600 // 60)


class ExtensionsMixin:
    # ───────── инициализация ─────────
    def init_extensions(self, save_dir, game_version):
        """Создаёт менеджеры. Любая ошибка не должна мешать запуску игры."""
        self.game_version = game_version
        self.cfg = ConfigManager(save_dir)
        self.profile = PlayerProfile(save_dir)
        self.notify = NotificationManager()
        self.http = HttpClient(cache_dir=save_dir, timeout=self.cfg.get("net_timeout"))
        self.bg = BackgroundFetcher()
        self.favorites = ServerStore(save_dir, "favorites.json", 50)
        self.recent = ServerStore(save_dir, "recent_servers.json", 10)
        self.offline = False
        self.update_info = None
        self.news_items, self.news_cached, self.news_error = [], False, None
        self.sb_servers, self.sb_errors, self.sb_index = [], {}, 0
        self.sb_last_refresh = 0.0
        self.mp_last_target = None
        self.net_reconnects = 0
        self.info_index = 0
        self.ns_index = 0
        self.ns_editing = False
        self.net_lost_since = 0.0
        self.net_lost_target = None
        # язык и ник: конфиг подхватывает старый save.json
        self.mp_ip_text = self.cfg.get("server_ip") if self.cfg.get("server_ip") != "127.0.0.1" else self.mp_ip_text
        self.mp_port_text = str(self.cfg.get("server_port"))
        if self.cfg.get("update_check") and self.cfg.get("update_url"):
            mgr = UpdateManager(self.http, game_version, self.cfg.get("update_url"))
            self.bg.submit("update", mgr.check)

    def tr(self, key, *args):
        return localization.tr(self.lang, key, *args)

    # ───────── фоновые задачи ─────────
    def poll_background(self):
        ok, info = self.bg.poll("update")
        if ok and info:
            self.update_info = info
            if info.error and info.error not in ("NO_URL",):
                self.offline = True
            if info.available:
                self.notify.info(self.tr("update_notice", info.latest))
        ok, res = self.bg.poll("news")
        if ok and res:
            self.news_items, self.news_cached, self.news_error = res
            if self.news_error and not self.news_items:
                self.offline = True
        ok, res = self.bg.poll("servers")
        if ok and res:
            self.sb_servers, self.sb_errors = res
            if self.sb_index >= len(self.sb_servers):
                self.sb_index = max(0, len(self.sb_servers) - 1)

    def build_discovery(self):
        import network as net
        providers = [LANProvider(lambda: self.net_browser, self.game_version),
                     StaticOnlineProvider(self.cfg.get("online_servers")),
                     LocalProvider(lambda: int(self.cfg.get("server_port")))]
        if self.cfg.get("online_master_enabled") and self.cfg.get("online_master_url"):
            providers.append(OnlineMasterProvider(self.cfg.get("online_master_url"), self.http))
        if self.cfg.get("web_servers_enabled"):
            for svc in self.cfg.get("web_services"):
                if svc.get("enabled", True):
                    cls = OfficialAPIProvider if svc.get("official") else CustomAPIProvider
                    providers.append(cls(svc, self.http))
        return ServerDiscovery(providers, self.favorites, self.recent)

    def refresh_servers(self, force=False):
        now = time.time()
        if not force and now - self.sb_last_refresh < float(self.cfg.get("rate_servers")):
            return
        if self.bg.busy("servers"):
            return
        self.sb_last_refresh = now
        disc = self.build_discovery()
        self.bg.submit("servers", disc.refresh)

    # ───────── подключение ─────────
    def begin_connect(self, ip, port):
        """Подключение к серверу тем же способом, что и существующий экран mp_join."""
        import network as net
        self.net_cleanup()
        self.mp_ip_text, self.mp_port_text = ip, str(port)
        self.mp_field, self.mp_error = "ip", None
        self.mp_connecting = True
        self.mp_connect_thread = threading.Thread(
            target=self._do_connect, args=(ip, int(port), self.net_my_name()), daemon=True)
        self.state = "mp_join"
        self.mp_connect_thread.start()

    def on_connect_ok(self):
        t = self.mp_last_target
        self.net_reconnects = 0
        self.notify.success(self.tr("net_connected"))
        if t:
            self.recent.add(t[0], t[1])
            self.cfg.set("last_server", "%s:%s" % t)
            self.cfg.set("server_ip", t[0])
            self.cfg.set("server_port", int(t[1]))
            self.cfg.save()

    def on_connect_fail(self):
        self.notify.error(self.tr("net_failed"))

    def validate_target(self, ip, port_text):
        """(ip, port, ошибка|None)"""
        try:
            ipaddress.ip_address(ip.strip())
        except ValueError:
            return None, None, self.tr("join_bad_ip")
        try:
            port = int(port_text or self.cfg.get("server_port"))
        except ValueError:
            return None, None, self.tr("join_bad_port")
        if not 1 <= port <= 65535:
            return None, None, self.tr("join_bad_port")
        return ip.strip(), port, None

    def join_cycle(self, store):
        """Подставляет следующий адрес из недавних/избранных в поля IP/порт."""
        if not store.items:
            return
        i = getattr(self, "_join_cycle_i", -1) + 1
        self._join_cycle_i = i % len(store.items)
        it = store.items[self._join_cycle_i]
        self.mp_ip_text, self.mp_port_text = it["address"], str(it["port"])

    def join_add_favorite(self):
        ip, port, err = self.validate_target(self.mp_ip_text, self.mp_port_text)
        if err:
            self.mp_error = err
            return
        self.favorites.add(ip, port)
        self.notify.success(self.tr("sb_fav_added"))

    # ───────── потеря соединения ─────────
    def enter_net_lost(self):
        if not self.mp_last_target:
            return False
        self.net_lost_target = self.mp_last_target
        self.net_lost_since = time.time()
        self.state = "net_lost"
        self.notify.error(self.tr("net_lost"))
        return True

    def handle_net_lost_input(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key in (pygame.K_RETURN, pygame.K_r):
            self.net_reconnects += 1
            self.begin_connect(*self.net_lost_target)
        elif event.key in (pygame.K_ESCAPE, pygame.K_m):
            self.state, self.menu_index = "menu", 0

    def update_net_lost(self, dt):
        # авто-переподключение: не более 3 попыток подряд, через 3 секунды
        if (self.cfg.get("auto_reconnect") and self.net_reconnects < 3
                and time.time() - self.net_lost_since > 3.0):
            self.net_reconnects += 1
            self.begin_connect(*self.net_lost_target)

    def draw_net_lost(self, surf):
        self.draw_menu_frame(surf, "net_lost")
        s = self.font_big.render(self.tr("net_lost"), True, RED_SOFT)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 260)))
        t = self.net_lost_target
        s = self.font_small.render("%s:%s" % t, True, GRAY)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 310)))
        for i, (key, hint) in enumerate((("net_reconnect", "ENTER"), ("net_to_menu", "ESC"))):
            txt = self.font_med.render("[%s] %s" % (hint, self.tr(key)), True, CYAN_SOFT if i == 0 else WHITE)
            surf.blit(txt, txt.get_rect(center=(LOGICAL_W // 2, 390 + i * 56)))

    # ───────── профиль ─────────
    def start_match_tracking(self):
        self.run_kills = self.run_deaths = 0
        self.run_time = 0.0
        self.run_won = False
        self.run_recorded = False
        self.run_lan = False

    def record_profile_match(self):
        """Один раз за матч; не роняет игру при ошибке."""
        if getattr(self, "run_recorded", True) or getattr(self, "run_time", 0) < 3:
            return
        self.run_recorded = True
        try:
            self.profile.nickname = self.cfg.get("nickname")
            new = self.profile.record_match(self.score, self.run_kills, self.run_deaths, self.run_time,
                                            self.wave, self.run_won, self.run_lan)
            for a in new:
                self.notify.success(self.tr("ach_unlocked", self.tr("ach_" + a)))
        except Exception as e:
            log.error("record_match: %s", e)

    def handle_stats_input(self, event):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_BACKSPACE):
            self.state = "menu"

    def draw_stats(self, surf):
        self.draw_menu_frame(surf, "stats")
        p, st = self.profile, self.profile.stats
        s = self.font_big.render(self.tr("stats_title"), True, CYAN_SOFT)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 170)))
        s = self.font_small.render("%s — %s" % (self.cfg.get("nickname"),
                                                self.tr("stat_level", p.level, p.xp, xp_for_level(p.level))), True, YELLOW)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 210)))
        rows = [("stat_kills", int(st["kills"])), ("stat_deaths", int(st["deaths"])), ("stat_kd", "%.2f" % p.kd()),
                ("stat_wins", int(st["wins"])), ("stat_losses", int(st["losses"])), ("stat_games", int(st["games"])),
                ("stat_time", fmt_time(st["play_time"])), ("stat_best", max(int(st["best_score"]), self.data["highscore"])),
                ("stat_lan", int(st["lan_games"]))]
        for i, (k, v) in enumerate(rows):
            col, row = i // 5, i % 5
            x = 110 + col * 410
            y = 250 + row * 34
            surf.blit(self.font_small.render(self.tr(k), True, GRAY), (x, y))
            surf.blit(self.font_small.render(str(v), True, WHITE), (x + 250, y))
        unlocked = set(p.unlocked)
        s = self.font_med.render(self.tr("ach_title", len(unlocked), len(ACHIEVEMENTS)), True, CYAN_SOFT)
        surf.blit(s, (110, 430))
        for i, a in enumerate(ACHIEVEMENTS):
            col, row = i // 5, i % 5
            got = a["id"] in unlocked
            label = ("[x] " if got else "[ ] ") + self.tr("ach_" + a["id"])
            surf.blit(self.font_tiny.render(label, True, GREEN if got else GRAY), (110 + col * 410, 470 + row * 22))
        h = self.font_tiny.render(self.tr("back_hint"), True, GRAY)
        surf.blit(h, h.get_rect(center=(LOGICAL_W // 2, LOGICAL_H - 24)))

    # ───────── новости / обновления ─────────
    def enter_info(self):
        self.info_index = 0
        self.state = "info"
        self.refresh_news()

    def refresh_news(self):
        if self.cfg.get("news_enabled") and self.cfg.get("news_url"):
            mgr = NewsManager(self.http, self.cfg.get("news_url"), float(self.cfg.get("rate_news")))
            self.bg.submit("news", mgr.fetch)

    def _link_url(self, cfg_key):
        return self.cfg.get(cfg_key)

    def handle_info_input(self, event):
        if event.type != pygame.KEYDOWN:
            return
        n = len(LINKS) + (1 if self.update_info and self.update_info.available and self.update_info.page_url else 0)
        if event.key == pygame.K_ESCAPE:
            self.state = "menu"
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.info_index = (self.info_index - 1) % n
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.info_index = (self.info_index + 1) % n
        elif event.key == pygame.K_r:
            self.refresh_news()
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            if self.info_index == len(LINKS):                      # кнопка «Открыть страницу обновления»
                open_in_browser(self.update_info.page_url)
                return
            url = self._link_url(LINKS[self.info_index][1])
            if not url or not open_in_browser(url):
                self.notify.warning(self.tr("link_unset"))

    def draw_info(self, surf):
        self.draw_menu_frame(surf, "info")
        s = self.font_big.render(self.tr("info_title"), True, CYAN_SOFT)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 160)))
        u = self.update_info
        if not self.cfg.get("update_check"):
            line, col = self.tr("update_disabled"), GRAY
        elif u is None or (u.error and not u.available):
            line, col = self.tr("update_unknown"), GRAY
        elif u.available:
            line, col = self.tr("update_available", u.current, u.latest), YELLOW
        else:
            line, col = self.tr("update_none", u.current), GREEN
        surf.blit(self.font_small.render(line, True, col), (80, 200))
        y = 228
        if u and u.available:
            surf.blit(self.font_tiny.render(self.tr("update_source", u.source[:70]), True, GRAY), (80, y))
            for ln in (u.changelog or "").splitlines()[:3]:
                y += 20
                surf.blit(self.font_tiny.render(ln[:90], True, WHITE), (100, y))
        # новости
        y = 300
        surf.blit(self.font_med.render(self.tr("news_header") + (" (%s)" % self.tr("news_cached") if self.news_cached else ""),
                                       True, CYAN_SOFT), (80, y))
        y += 36
        if not self.cfg.get("news_enabled"):
            surf.blit(self.font_small.render(self.tr("news_disabled"), True, GRAY), (80, y))
        elif not self.news_items:
            surf.blit(self.font_small.render(self.tr("news_offline"), True, GRAY), (80, y))
        else:
            for it in self.news_items[:4]:
                surf.blit(self.font_small.render("%s  %s" % (it["date"], it["title"]), True, WHITE), (80, y))
                y += 26
        # ссылки
        items = [(self.tr(k), self._link_url(c)) for k, c in LINKS]
        if u and u.available and u.page_url:
            items.append((self.tr("update_open"), u.page_url))
        base = 440
        for i, (label, url) in enumerate(items):
            sel = i == self.info_index
            txt = self.font_small.render(label + ("" if url else "  (%s)" % self.tr("link_unset")), True,
                                         CYAN_SOFT if sel else (WHITE if url else GRAY))
            r = txt.get_rect(topleft=(80, base + i * 28))
            if sel:
                pygame.draw.rect(surf, (*CYAN, 40), r.inflate(20, 8), border_radius=6)
            surf.blit(txt, r)
        h = self.font_tiny.render(self.tr("info_hint"), True, GRAY)
        surf.blit(h, h.get_rect(center=(LOGICAL_W // 2, LOGICAL_H - 24)))

    # ───────── браузер серверов ─────────
    def enter_server_browser(self):
        import network as net
        self.net_cleanup()
        self.net_browser = net.LobbyBrowser()
        try:
            self.net_browser.start()
        except Exception:
            self.net_browser = None
        self.sb_index = 0
        self.sb_servers, self.sb_errors = [], {}
        self.sb_last_refresh = 0.0
        self.refresh_servers(force=True)
        self.state = "server_browser"

    def update_server_browser(self, dt):
        self.refresh_servers()           # авто-обновление с учётом rate limit

    def handle_server_browser_input(self, event):
        if event.type != pygame.KEYDOWN:
            return
        n = len(self.sb_servers)
        sel = self.sb_servers[self.sb_index] if 0 <= self.sb_index < n else None
        if event.key == pygame.K_ESCAPE:
            self.net_cleanup()
            self.state = "mp_menu"
        elif event.key in (pygame.K_UP, pygame.K_w) and n:
            self.sb_index = (self.sb_index - 1) % n
        elif event.key in (pygame.K_DOWN, pygame.K_s) and n:
            self.sb_index = (self.sb_index + 1) % n
        elif event.key == pygame.K_r:
            self.refresh_servers(force=True)
        elif event.key == pygame.K_i:
            self.enter_mp_join()
        elif event.key == pygame.K_f and sel:
            if self.favorites.has(sel.address, sel.port):
                self.favorites.remove(sel.address, sel.port)
                self.notify.info(self.tr("sb_fav_removed"))
            else:
                self.favorites.add(sel.address, sel.port, sel.name)
                self.notify.success(self.tr("sb_fav_added"))
        elif event.key == pygame.K_DELETE and sel and sel.source in ("FAV", "RECENT"):
            (self.favorites if sel.source == "FAV" else self.recent).remove(sel.address, sel.port)
            self.refresh_servers(force=True)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE) and sel:
            self.play_sound("confirm")
            self.begin_connect(sel.address, sel.port)

    def draw_server_browser(self, surf):
        self.draw_menu_frame(surf, "sb")
        s = self.font_big.render(self.tr("sb_title"), True, CYAN_SOFT)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 160)))
        surf.blit(self.font_tiny.render(self.tr("sb_header"), True, GRAY), (60, 205))
        y = 232
        if not self.sb_servers:
            msg = self.tr("sb_loading") if self.bg.busy("servers") else self.tr("sb_empty")
            surf.blit(self.font_small.render(msg, True, GRAY), (60, y))
        top = max(0, self.sb_index - 9)
        for i, sv in enumerate(self.sb_servers[top:top + 10]):
            idx = top + i
            sel = idx == self.sb_index
            fav = "*" if self.favorites.has(sv.address, sv.port) else " "
            line = "%s%-14s %-17s %d/%-6d %-8s %s%s" % (
                fav, sv.name[:14], "%s:%s" % (sv.address[:12], sv.port), sv.players, sv.max_players,
                ("%dms" % sv.ping) if sv.ping >= 0 else "—", sv.source, " (cache)" if sv.cached else "")
            if not sv.online or (sv.ping < 0 and sv.source in ("FAV", "RECENT")):
                line += "  " + self.tr("sb_offline")
            txt = self.font_tiny.render(line, True, CYAN_SOFT if sel else WHITE)
            r = txt.get_rect(topleft=(60, y))
            if sel:
                pygame.draw.rect(surf, (*CYAN, 40), r.inflate(16, 8), border_radius=6)
            surf.blit(txt, r)
            y += 28
        sel = self.sb_servers[self.sb_index] if self.sb_servers else None
        if sel and (sel.map or sel.mode or sel.version or sel.description):
            d = "%s %s %s %s" % (sel.map, sel.mode, sel.version, sel.description)
            surf.blit(self.font_tiny.render(d.strip()[:100], True, GRAY), (60, 530))
        ey = 556
        for src, err in list(self.sb_errors.items())[:2]:
            key = "sb_cached" if err == "CACHED" else "sb_source_down"
            surf.blit(self.font_tiny.render(self.tr(key, src), True, YELLOW), (60, ey))
            ey += 18
        h = self.font_tiny.render(self.tr("sb_hint"), True, GRAY)
        surf.blit(h, h.get_rect(center=(LOGICAL_W // 2, LOGICAL_H - 20)))

    # ───────── сеть и профиль: настройки ─────────
    NS_ROWS = ["nickname", "net_timeout", "auto_reconnect", "update_check", "news_enabled",
               "web_servers_enabled", "volume", "sensitivity", "clear_recent", "back"]

    def _ns_value(self, key):
        v = self.cfg.get(key)
        if key in ("auto_reconnect", "update_check", "news_enabled", "web_servers_enabled"):
            return self.tr("ns_on") if v else self.tr("ns_off")
        if key == "net_timeout":
            return "%.0f" % v
        if key in ("volume", "sensitivity"):
            return "%.1f" % v
        return str(v)

    def handle_netset_input(self, event):
        if event.type != pygame.KEYDOWN:
            return
        key = self.NS_ROWS[self.ns_index]
        if self.ns_editing:                         # правка ника
            if event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
                self.ns_editing = False
                self.cfg.set("nickname", (self.cfg.get("nickname").strip() or "Player"))
            elif event.key == pygame.K_BACKSPACE:
                self.cfg.data["nickname"] = self.cfg.get("nickname")[:-1]
            elif event.unicode and event.unicode.isprintable() and len(self.cfg.get("nickname")) < 16:
                self.cfg.data["nickname"] = self.cfg.get("nickname") + event.unicode
                self.cfg._dirty = True
            return
        if event.key in (pygame.K_UP, pygame.K_w):
            self.ns_index = (self.ns_index - 1) % len(self.NS_ROWS)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.ns_index = (self.ns_index + 1) % len(self.NS_ROWS)
        elif event.key == pygame.K_ESCAPE or (event.key == pygame.K_RETURN and key == "back"):
            self.cfg.save()
            self.state = "settings"
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d, pygame.K_RETURN):
            d = -1 if event.key in (pygame.K_LEFT, pygame.K_a) else 1
            if key == "nickname" and event.key == pygame.K_RETURN:
                self.ns_editing = True
            elif key in ("auto_reconnect", "update_check", "news_enabled", "web_servers_enabled"):
                self.cfg.set(key, not self.cfg.get(key))
            elif key == "net_timeout":
                self.cfg.set(key, max(1.0, min(30.0, self.cfg.get(key) + d)))
            elif key in ("volume", "sensitivity"):
                self.cfg.set(key, round(max(0.0, min(3.0 if key == "sensitivity" else 1.0, self.cfg.get(key) + 0.1 * d)), 1))
                if key == "volume":
                    self.apply_volume()
            elif key == "clear_recent" and event.key == pygame.K_RETURN:
                self.recent.clear()
                self.notify.info(self.tr("recent_cleared"))

    def apply_volume(self):
        try:
            import pygame as _pg
            for snd in getattr(self, "sounds", {}).values():
                if snd:
                    snd.set_volume(float(self.cfg.get("volume")) * float(self.cfg.get("effects_volume")))
        except Exception:
            pass

    def draw_netset(self, surf):
        self.draw_menu_frame(surf, "ns")
        s = self.font_big.render(self.tr("ns_title"), True, CYAN_SOFT)
        surf.blit(s, s.get_rect(center=(LOGICAL_W // 2, 160)))
        labels = {"nickname": "ns_nickname", "net_timeout": "ns_timeout", "auto_reconnect": "ns_reconnect",
                  "update_check": "ns_updates", "news_enabled": "ns_news", "web_servers_enabled": "ns_web",
                  "volume": "ns_volume", "sensitivity": "ns_sens", "clear_recent": "ns_clear_recent",
                  "back": "mp_back"}
        for i, key in enumerate(self.NS_ROWS):
            sel = i == self.ns_index
            val = self._ns_value(key) if key not in ("clear_recent", "back") else ""
            if key == "nickname" and self.ns_editing and (pygame.time.get_ticks() // 400) % 2 == 0:
                val += "_"
            label = self.tr(labels[key]) + ("   ◄ %s ►" % val if val else "")
            txt = self.font_small.render(label, True, CYAN_SOFT if sel else WHITE)
            r = txt.get_rect(center=(LOGICAL_W // 2, 220 + i * 38))
            if sel:
                pygame.draw.rect(surf, (*CYAN, 35), r.inflate(40, 12), border_radius=8)
            surf.blit(txt, r)
        h = self.font_tiny.render(self.tr("ns_hint"), True, GRAY)
        surf.blit(h, h.get_rect(center=(LOGICAL_W // 2, LOGICAL_H - 24)))

    # ───────── общий оверлей ─────────
    def draw_overlay(self, surf):
        self.notify.draw(surf, self.font_tiny, LOGICAL_W)
        if self.offline and self.state == "menu":
            b = self.font_tiny.render(self.tr("offline_mode"), True, YELLOW)
            surf.blit(b, (10, LOGICAL_H - 20))
