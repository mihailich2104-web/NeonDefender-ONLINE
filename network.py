#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════════════
# NEON DEFENDER — модуль локального (LAN) сетевого кооператива.
#
# Архитектура: HOST держит авторитетное состояние матча (враги, волны,
# столкновения, очки), CLIENT только отправляет свой ввод и отрисовывает
# то, что прислал HOST. Транспорт — TCP, сообщения — JSON-строки,
# разделённые "\n". Сеть работает в отдельных потоках и никогда не
# блокирует основной игровой цикл Pygame.
#
# Никакого интернета, внешних серверов и облака не используется —
# только сокеты внутри локальной сети (Wi-Fi/Ethernet).
# ═══════════════════════════════════════════════════════════════════════

import socket
import threading
import json
import time
import queue

NETWORK_PROTOCOL_VERSION = 1
DEFAULT_PORT = 5000
DISCOVERY_PORT = 5001          # отдельный UDP-порт для объявлений лобби
DISCOVERY_MAGIC = "NEON_DEFENDER_LOBBY_V1"
DISCOVERY_INTERVAL = 1.0       # как часто HOST рассылает "я тут"
DISCOVERY_TTL = 4.0            # через сколько сек. лобби пропадает из списка, если тишина
MAX_NAME_LEN = 16


def get_local_ip():
    """Определяет реальный LAN IP этого компьютера (не 127.0.0.1).
    Работает и без доступа в интернет: UDP-сокет не отправляет пакет
    при connect(), только выбирает исходящий сетевой интерфейс по
    таблице маршрутизации."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    if ip.startswith("127."):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass
    return ip


def get_local_ips():
    """Возвращает список всех локальных IPv4-адресов этого компьютера —
    включая виртуальные адаптеры вроде Radmin VPN, Hamachi и т.п. Нужен
    для того, чтобы разослать UDP-объявление лобби во ВСЕ сети сразу, а
    не только в ту, что выбрана "по умолчанию"."""
    ips = set()
    try:
        ips.add(get_local_ip())
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        _, _, addrs = socket.gethostbyname_ex(hostname)
        for a in addrs:
            if a and not a.startswith("127."):
                ips.add(a)
    except Exception:
        pass
    try:
        # Второй способ через AF_INET-сокеты на разные "внешние" цели —
        # заставляет ОС выбрать разные исходящие интерфейсы (в т.ч. VPN).
        for target in ("10.255.255.255", "26.255.255.255", "192.168.255.255"):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((target, 1))
                ip = s.getsockname()[0]
                s.close()
                if ip and not ip.startswith("127."):
                    ips.add(ip)
            except Exception:
                pass
    except Exception:
        pass
    return sorted(ips) if ips else ["127.0.0.1"]


def _broadcast_addrs_for(ip):
    """Из адреса вида a.b.c.d строит широковещательный a.b.c.255 (маска
    /24 — верно для подавляющего большинства домашних/LAN и Radmin VPN
    сетей) плюс сам глобальный 255.255.255.255."""
    parts = ip.split(".")
    out = {"255.255.255.255"}
    if len(parts) == 4:
        out.add(".".join(parts[:3]) + ".255")
    return out


def _send_line(sock, obj):
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    sock.sendall(data)


class _LineReader:
    """Читает JSON-сообщения, разделённые \\n, из потокового сокета."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read_messages(self):
        msgs = []
        try:
            data = self.sock.recv(65536)
        except socket.timeout:
            return msgs
        except OSError:
            raise ConnectionError("socket closed")
        if not data:
            raise ConnectionError("connection closed")
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if not line:
                continue
            try:
                msgs.append(json.loads(line.decode("utf-8")))
            except Exception:
                pass
        return msgs


class LANServer:
    """LAN-сервер (HOST). Принимает до (max_players-1) клиентов
    (сам HOST — игрок с id=1)."""

    def __init__(self, port=DEFAULT_PORT, max_players=2):
        self.port = port
        self.max_players = max_players
        self.sock = None
        self.running = False
        self.clients = {}  # id -> dict(sock, reader, name)
        self.next_id = 2
        self.lock = threading.Lock()
        self.incoming = queue.Queue()
        self.events = queue.Queue()
        self._accept_thread = None

    def start(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.listen(8)
            self.sock.settimeout(0.5)
            try:
                # Отключаем алгоритм Нейгла — без этого TCP может держать
                # мелкие пакеты (позиции/ввод) до ~40мс, из-за чего LAN-игра
                # ощутимо "лагает" даже в идеальной сети.
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
        except OSError as e:
            raise ConnectionError("PORT_ERROR: %s" % e)
        self.running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                full = len(self.clients) >= (self.max_players - 1)
                pid = self.next_id
                if not full:
                    self.next_id += 1
            if full:
                try:
                    _send_line(conn, {"type": "error", "reason": "SERVER_FULL"})
                    conn.close()
                except Exception:
                    pass
                continue
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            conn.settimeout(0.5)
            client = {"sock": conn, "reader": _LineReader(conn), "name": "Player %d" % pid, "addr": addr}
            with self.lock:
                self.clients[pid] = client
            threading.Thread(target=self._client_loop, args=(pid, client), daemon=True).start()

    def _client_loop(self, pid, client):
        sock = client["sock"]
        reader = client["reader"]
        try:
            _send_line(sock, {"type": "welcome", "player_id": pid, "protocol": NETWORK_PROTOCOL_VERSION})
        except Exception:
            pass
        while self.running:
            try:
                msgs = reader.read_messages()
            except Exception:
                break
            for m in msgs:
                if m.get("type") == "hello":
                    if m.get("protocol") != NETWORK_PROTOCOL_VERSION:
                        try:
                            _send_line(sock, {"type": "error", "reason": "VERSION_MISMATCH"})
                        except Exception:
                            pass
                        self.running_client = False
                        break
                    name = str(m.get("name", "") or "")[:MAX_NAME_LEN].strip() or ("Player %d" % pid)
                    with self.lock:
                        client["name"] = name
                    self.events.put(("connected", pid, name))
                else:
                    m["_player_id"] = pid
                    self.incoming.put(m)
            time.sleep(0.005)
        with self.lock:
            self.clients.pop(pid, None)
        try:
            sock.close()
        except Exception:
            pass
        self.events.put(("disconnected", pid, None))

    def broadcast(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self.lock:
            items = list(self.clients.items())
        dead = []
        for pid, c in items:
            try:
                c["sock"].sendall(data)
            except Exception:
                dead.append(pid)
        if dead:
            with self.lock:
                for pid in dead:
                    self.clients.pop(pid, None)

    def get_incoming(self):
        out = []
        while True:
            try:
                out.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return out

    def get_events(self):
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    def player_names(self):
        with self.lock:
            return {pid: c["name"] for pid, c in self.clients.items()}

    def player_count(self):
        with self.lock:
            return len(self.clients)

    def stop(self):
        self.running = False
        with self.lock:
            clients = list(self.clients.values())
            self.clients.clear()
        for c in clients:
            try:
                c["sock"].close()
            except Exception:
                pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class LANClient:
    """LAN-клиент. Подключается к HOST по IP:порт."""

    def __init__(self, name="Player"):
        self.sock = None
        self.reader = None
        self.running = False
        self.connected = False
        self.player_id = None
        self.name = (name or "Player")[:MAX_NAME_LEN]
        self.incoming = queue.Queue()
        self._thread = None

    def connect(self, ip, port=DEFAULT_PORT, timeout=4.0):
        """Блокирующий вызов — запускать в отдельном потоке, чтобы не
        морозить игровой цикл."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, int(port)))
        except socket.timeout:
            raise ConnectionError("SERVER_NOT_FOUND")
        except ConnectionRefusedError:
            raise ConnectionError("CONNECTION_ERROR")
        except OSError:
            raise ConnectionError("CONNECTION_FAILED")
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        s.settimeout(0.5)
        self.sock = s
        self.reader = _LineReader(s)
        try:
            _send_line(s, {"type": "hello", "name": self.name, "protocol": NETWORK_PROTOCOL_VERSION})
        except Exception:
            raise ConnectionError("CONNECTION_FAILED")
        self.running = True
        self.connected = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def _recv_loop(self):
        while self.running:
            try:
                msgs = self.reader.read_messages()
            except Exception:
                self.connected = False
                self.incoming.put({"type": "disconnected"})
                return
            for m in msgs:
                if m.get("type") == "welcome":
                    self.player_id = m.get("player_id")
                self.incoming.put(m)
            time.sleep(0.005)

    def send(self, obj):
        if not self.connected or not self.sock:
            return
        try:
            _send_line(self.sock, obj)
        except Exception:
            self.connected = False

    def get_incoming(self):
        out = []
        while True:
            try:
                out.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return out

    def disconnect(self):
        self.running = False
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class LobbyAdvertiser:
    """Работает на стороне HOST: раз в DISCOVERY_INTERVAL секунд рассылает
    по UDP-широковещанию короткое объявление о созданной игре — во все
    известные локальные подсети сразу (обычный Wi-Fi/Ethernet + виртуальные
    адаптеры вроде Radmin VPN/Hamachi), чтобы клиенты видели игру в лобби
    списком и не вводили IP руками."""

    def __init__(self, game_port, name, max_players=2):
        self.game_port = game_port
        self.name = (name or "Game")[:MAX_NAME_LEN]
        self.max_players = max_players
        self.running = False
        self.sock = None
        self._thread = None
        self.get_player_count = lambda: 1

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self.running:
            try:
                payload = json.dumps({
                    "magic": DISCOVERY_MAGIC,
                    "protocol": NETWORK_PROTOCOL_VERSION,
                    "name": self.name,
                    "port": self.game_port,
                    "players": self.get_player_count(),
                    "max_players": self.max_players,
                }, ensure_ascii=False).encode("utf-8")
                targets = set()
                for ip in get_local_ips():
                    targets |= _broadcast_addrs_for(ip)
                for addr in targets:
                    try:
                        self.sock.sendto(payload, (addr, DISCOVERY_PORT))
                    except Exception:
                        pass
            except Exception:
                pass
            for _ in range(int(DISCOVERY_INTERVAL / 0.1) or 1):
                if not self.running:
                    break
                time.sleep(0.1)

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class LobbyBrowser:
    """Работает на стороне подключающегося игрока: слушает UDP-порт
    DISCOVERY_PORT и собирает список игр, объявленных LobbyAdvertiser'ом
    в локальной сети (включая Radmin VPN и т.п.). Список автоматически
    "стареет" — игра пропадает, если от неё давно не было объявлений
    (хост закрыл лобби / сеть недоступна)."""

    def __init__(self):
        self.running = False
        self.sock = None
        self._thread = None
        self.lock = threading.Lock()
        self.games = {}  # (ip, port) -> dict(name, players, max_players, last_seen)

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("0.0.0.0", DISCOVERY_PORT))
        except OSError:
            # Порт уже занят (например, другой экземпляр игры на этом же
            # ПК) — тихо продолжаем без обнаружения, останется ручной ввод.
            self.sock = None
            return
        self.sock.settimeout(0.5)
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                self._prune()
                continue
            except OSError:
                break
            try:
                obj = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if obj.get("magic") != DISCOVERY_MAGIC:
                continue
            ip = addr[0]
            port = obj.get("port", DEFAULT_PORT)
            with self.lock:
                self.games[(ip, port)] = {
                    "name": obj.get("name", "Game"),
                    "players": obj.get("players", 1),
                    "max_players": obj.get("max_players", 2),
                    "protocol": obj.get("protocol", NETWORK_PROTOCOL_VERSION),
                    "last_seen": time.time(),
                }
            self._prune()

    def _prune(self):
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.games.items() if now - v["last_seen"] > DISCOVERY_TTL]
            for k in stale:
                self.games.pop(k, None)

    def list_games(self):
        with self.lock:
            out = []
            for (ip, port), v in self.games.items():
                item = dict(v)
                item["ip"] = ip
                item["port"] = port
                out.append(item)
        out.sort(key=lambda g: (g["name"], g["ip"]))
        return out

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
