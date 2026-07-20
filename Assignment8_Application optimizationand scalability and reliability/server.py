#!/usr/bin/env python3

import socket
import threading
import datetime
import csv
import os
import hashlib
import json
import time
import signal
import sys
from concurrent.futures import ThreadPoolExecutor

# ── Configuration loading ────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0", "port": 5000, "thread_pool_size": 25,
        "max_clients": 20, "inactivity_timeout": 300,
        "timeout_check_interval": 30, "socket_recv_timeout": 120,
        "max_msg_size": 500, "max_fail_attempts": 5,
        "lockout_seconds": 60, "listen_backlog": 50,
    },
    "files": {
        "users_file": "users.csv", "security_log": "security_log.txt",
        "chat_history": "chat_history.csv", "server_log": "server_log.txt",
    },
}


def load_config():
    """Load config.json; fall back to defaults (with a warning) if missing
    or malformed, so the server never refuses to start over a config typo."""
    if not os.path.exists(CONFIG_PATH):
        print(f'[CONFIG] {CONFIG_PATH} not found — using built-in defaults.')
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
        # Shallow-merge with defaults so a partial config.json still works
        merged = {**DEFAULT_CONFIG, **cfg}
        merged['server'] = {**DEFAULT_CONFIG['server'], **cfg.get('server', {})}
        merged['files'] = {**DEFAULT_CONFIG['files'], **cfg.get('files', {})}
        return merged
    except (json.JSONDecodeError, OSError) as e:
        print(f'[CONFIG] Failed to parse {CONFIG_PATH} ({e}) — using defaults.')
        return DEFAULT_CONFIG


CFG = load_config()
S = CFG['server']
F = CFG['files']

HOST                = S['host']
PORT                = S['port']
THREAD_POOL_SIZE    = S['thread_pool_size']
MAX_CLIENTS         = S['max_clients']
INACTIVITY_TIMEOUT  = S['inactivity_timeout']
TIMEOUT_CHECK_EVERY = S['timeout_check_interval']
SOCKET_RECV_TIMEOUT = S['socket_recv_timeout']
MAX_MSG_SIZE        = S['max_msg_size']
MAX_FAIL_ATTEMPTS   = S['max_fail_attempts']
LOCKOUT_SECONDS     = S['lockout_seconds']
LISTEN_BACKLOG      = S['listen_backlog']

USERS_FILE   = F['users_file']
SECURITY_LOG = F['security_log']
CHAT_HISTORY = F['chat_history']
SERVER_LOG   = F['server_log']

# ── Shared state ──────────────────────────────────────────────────────
clients        = {}     # socket -> {username, ip, port, login_time, last_active}
clients_lock   = threading.Lock()

logged_in      = set()  # usernames currently logged in (duplicate prevention)
logged_in_lock = threading.Lock()

failed_attempts = {}    # ip -> {count, lockout_until}
fail_lock       = threading.Lock()

stats = {'total_connected': 0, 'messages_processed': 0,
         'broadcast_messages': 0, 'private_messages': 0,
         'connections_rejected': 0, 'clients_timed_out': 0,
         'clients_disconnected_abnormally': 0}
stats_lock = threading.Lock()

log_lock = threading.Lock()

shutdown_event = threading.Event()   # Task 2: graceful shutdown flag
server_socket = None
executor = None


# ── Initialise files ──────────────────────────────────────────────────
def init_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['username', 'password_hash'])
        register_user('admin', 'admin123')

    if not os.path.exists(CHAT_HISTORY):
        with open(CHAT_HISTORY, 'w', newline='') as f:
            csv.writer(f).writerow(
                ['timestamp', 'sender', 'receiver', 'message_type', 'message'])


# ── Password hashing ─────────────────────────────────────────────────
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# ── User management ─────────────────────────────────────────────────
def register_user(username, password):
    ph = hash_password(password)
    with open(USERS_FILE, 'a', newline='') as f:
        csv.writer(f).writerow([username, ph])


def load_users():
    users = {}
    try:
        with open(USERS_FILE, 'r', newline='') as f:
            for row in csv.DictReader(f):
                users[row['username']] = row['password_hash']
    except FileNotFoundError:
        pass
    return users


def verify_user(username, password):
    users = load_users()
    if username not in users:
        return False
    return users[username] == hash_password(password)


def user_exists(username):
    return username in load_users()


# ── Input validation ─────────────────────────────────────────────────
def validate_username(username):
    if not username or len(username) < 3:
        return False, 'Username must be at least 3 characters.'
    if len(username) > 20:
        return False, 'Username too long (max 20 characters).'
    if ' ' in username:
        return False, 'Username cannot contain spaces.'
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
    if not all(c in allowed for c in username):
        return False, 'Username can only contain letters, numbers, and underscores.'
    return True, 'OK'


def validate_password(password):
    if not password or len(password) < 4:
        return False, 'Password must be at least 4 characters.'
    if len(password) > 50:
        return False, 'Password too long (max 50 characters).'
    return True, 'OK'


def validate_message(message):
    if len(message) > MAX_MSG_SIZE:
        return False, f'Message too long (max {MAX_MSG_SIZE} characters).'
    SUPPORTED_COMMANDS = ['/list', '/stats', '/help', '/logout', '/msg ']
    if message.startswith('/'):
        if not any(message.startswith(cmd) for cmd in SUPPORTED_COMMANDS):
            return False, 'Unknown command. Type /help for available commands.'
    return True, 'OK'


# ── Lockout management ───────────────────────────────────────────────
def is_locked_out(ip):
    with fail_lock:
        if ip not in failed_attempts:
            return False
        info = failed_attempts[ip]
        if info.get('lockout_until') and time.time() < info['lockout_until']:
            remaining = int(info['lockout_until'] - time.time())
            return True, remaining
        return False


def record_failed_attempt(ip):
    with fail_lock:
        if ip not in failed_attempts:
            failed_attempts[ip] = {'count': 0, 'lockout_until': None}
        failed_attempts[ip]['count'] += 1
        if failed_attempts[ip]['count'] >= MAX_FAIL_ATTEMPTS:
            failed_attempts[ip]['lockout_until'] = time.time() + LOCKOUT_SECONDS
            failed_attempts[ip]['count'] = 0
            return True
    return False


def reset_failed_attempts(ip):
    with fail_lock:
        if ip in failed_attempts:
            failed_attempts[ip] = {'count': 0, 'lockout_until': None}


# ── Logging ───────────────────────────────────────────────────────────
def ts():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_security(event, username, ip, detail=''):
    line = f"{ts()} | {event:25s} | {username:20s} | {ip:15s} | {detail}\n"
    with log_lock:
        with open(SECURITY_LOG, 'a') as f:
            f.write(line)
    print(line.strip())


def log_server(event, username, ip, detail=''):
    line = f"{ts()},{event},{username},{ip},{detail}\n"
    with log_lock:
        with open(SERVER_LOG, 'a') as f:
            f.write(line)


def log_chat(sender, receiver, msg_type, message):
    with log_lock:
        with open(CHAT_HISTORY, 'a', newline='') as f:
            csv.writer(f).writerow([ts(), sender, receiver, msg_type, message])


# ── Messaging helpers ─────────────────────────────────────────────────
def send_to(sock, msg):
    """Best-effort send. Returns False (instead of raising) on failure so
    callers can treat a dead peer as a normal, expected condition."""
    try:
        sock.sendall((msg + '\n').encode())
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False


def broadcast(message, exclude_sock=None):
    dead = []
    with clients_lock:
        targets = [s for s in clients.keys() if s != exclude_sock]
    for s in targets:
        if not send_to(s, message):
            dead.append(s)
    # Task 1: proactively reap sockets that failed mid-broadcast
    for s in dead:
        cleanup_client(s, reason='Broken pipe during broadcast')


def find_socket(username):
    with clients_lock:
        for s, info in clients.items():
            if info['username'] == username:
                return s
    return None


def build_user_list():
    with clients_lock:
        names = [info['username'] for info in clients.values()]
    return 'Online users: ' + ', '.join(names) if names else 'No users online.'


def build_stats():
    with stats_lock:
        s = stats.copy()
    with clients_lock:
        online = len(clients)
    return (f"=== Server Statistics ===\n"
            f"Currently online   : {online}\n"
            f"Total connected    : {s['total_connected']}\n"
            f"Messages processed : {s['messages_processed']}\n"
            f"Broadcast messages : {s['broadcast_messages']}\n"
            f"Private messages   : {s['private_messages']}\n"
            f"Connections queued/rejected: {s['connections_rejected']}\n"
            f"Clients timed out  : {s['clients_timed_out']}\n"
            f"Abnormal disconnects: {s['clients_disconnected_abnormally']}\n"
            f"=========================")


def get_last_messages(username, n=5):
    rows = []
    try:
        with open(CHAT_HISTORY, 'r', newline='') as f:
            for row in csv.DictReader(f):
                if row['sender'] == username:
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows[-n:]


# ── Task 1: centralised, idempotent client cleanup ─────────────────────
def cleanup_client(client_sock, username=None, reason='', abnormal=False):
    """Removes a client from every shared structure and releases the
    socket. Safe to call more than once for the same socket — only the
    call that actually pops the client from `clients` does any logging
    or broadcasting, so concurrent cleanup attempts (e.g. a broken-pipe
    during broadcast racing with the inactivity sweeper) never double-log
    or recurse into each other while holding a lock."""
    with clients_lock:
        info = clients.pop(client_sock, None)

    try:
        client_sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        client_sock.close()
    except OSError:
        pass

    if info is None:
        return  # already cleaned up via another code path

    uname = username or info['username']
    ip = info['ip']

    with logged_in_lock:
        if uname in logged_in:
            logged_in.remove(uname)

    log_server('DISCONNECTED', uname, ip, reason)
    if abnormal:
        log_security('CONNECTION_LOST', uname, ip, reason or 'Client disconnected abnormally')
        with stats_lock:
            stats['clients_disconnected_abnormally'] += 1
    broadcast(f'*** {uname} has left the chat ***')


# ── Task 1 & 2: inactivity / dead-connection sweeper ───────────────────
def check_timeouts():
    """Runs in the background. Kicks clients that have gone silent for
    longer than INACTIVITY_TIMEOUT, releasing their resources cleanly."""
    while not shutdown_event.is_set():
        shutdown_event.wait(TIMEOUT_CHECK_EVERY)
        if shutdown_event.is_set():
            break
        now = time.time()
        with clients_lock:
            to_kick = [(s, info['username']) for s, info in clients.items()
                       if now - info.get('last_active', now) > INACTIVITY_TIMEOUT]
        for s, uname in to_kick:
            send_to(s, '[SERVER] You have been disconnected due to inactivity.')
            with stats_lock:
                stats['clients_timed_out'] += 1
            cleanup_client(s, username=uname, reason='Inactivity timeout')


# ── Per-client handler (runs inside the thread pool — Task 3) ─────────
def handle_client(client_sock, addr):
    client_ip = addr[0]
    username = None
    client_sock.settimeout(SOCKET_RECV_TIMEOUT)

    try:
        lockout = is_locked_out(client_ip)
        if lockout:
            _, remaining = lockout
            send_to(client_sock, f'AUTH_FAIL|Too many failed attempts. Try again in {remaining} seconds.')
            log_security('LOCKOUT_BLOCKED', 'unknown', client_ip, f'{remaining}s remaining')
            cleanup_client(client_sock, reason='Locked out')
            return

        try:
            send_to(client_sock, 'AUTH_REQUEST|Enter username:')
            raw_un = client_sock.recv(1024).decode().strip()
            send_to(client_sock, 'AUTH_REQUEST|Enter password:')
            raw_pw = client_sock.recv(1024).decode().strip()
        except socket.timeout:
            log_security('AUTH_TIMEOUT', 'unknown', client_ip, 'Client did not respond in time')
            cleanup_client(client_sock, reason='Auth timeout')
            return
        except (ConnectionResetError, OSError) as e:
            log_security('AUTH_ABORTED', 'unknown', client_ip, str(e))
            cleanup_client(client_sock, reason=f'Connection error during auth: {e}')
            return

        if not raw_un or not raw_pw:
            send_to(client_sock, 'AUTH_FAIL|Connection closed before credentials were received.')
            cleanup_client(client_sock, reason='Empty credentials (client dropped)')
            return

        ok, msg = validate_username(raw_un)
        if not ok:
            send_to(client_sock, f'AUTH_FAIL|{msg}')
            log_security('INVALID_USERNAME', raw_un, client_ip, msg)
            cleanup_client(client_sock, reason='Invalid username')
            return

        ok, msg = validate_password(raw_pw)
        if not ok:
            send_to(client_sock, f'AUTH_FAIL|{msg}')
            log_security('INVALID_PASSWORD', raw_un, client_ip, msg)
            cleanup_client(client_sock, reason='Invalid password')
            return

        if not user_exists(raw_un):
            register_user(raw_un, raw_pw)
            log_security('USER_REGISTERED', raw_un, client_ip)
        else:
            if not verify_user(raw_un, raw_pw):
                locked = record_failed_attempt(client_ip)
                log_security('AUTH_FAILED', raw_un, client_ip, 'Wrong password')
                if locked:
                    send_to(client_sock, f'AUTH_FAIL|Too many failed attempts. Locked for {LOCKOUT_SECONDS} seconds.')
                else:
                    with fail_lock:
                        count = failed_attempts.get(client_ip, {}).get('count', 0)
                    remaining = MAX_FAIL_ATTEMPTS - count
                    send_to(client_sock, f'AUTH_FAIL|Wrong password. {remaining} attempt(s) remaining.')
                cleanup_client(client_sock, reason='Failed authentication')
                return

        with logged_in_lock:
            if raw_un in logged_in:
                send_to(client_sock, 'AUTH_FAIL|This user is already logged in.')
                log_security('DUPLICATE_LOGIN', raw_un, client_ip)
                cleanup_client(client_sock, reason='Duplicate login')
                return
            logged_in.add(raw_un)

        reset_failed_attempts(client_ip)
        username = raw_un
        login_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        log_security('AUTH_SUCCESS', username, client_ip)
        log_server('CONNECTED', username, client_ip)

        # Send this client's own AUTH_OK, history, and help text FIRST,
        # and only register it in `clients` (making it a broadcast target)
        # AFTER that. Otherwise, under concurrent logins, another client's
        # join-announcement broadcast can be scheduled and land on this
        # socket before this client's own AUTH_OK does — the client would
        # then read "*** other_user has joined the chat ***" where it
        # expected AUTH_OK, and fail to authenticate even though the
        # server considers it logged in. (Discovered via load_test.py at
        # 8 concurrent clients — see report, Task 1 / Task 3.)
        send_to(client_sock, f'AUTH_OK|Welcome, {username}!')

        history = get_last_messages(username, 5)
        if history:
            send_to(client_sock, f'--- Your last {len(history)} messages ---')
            for row in history:
                send_to(client_sock, f"[{row['timestamp']}] [{row['message_type']}] to {row['receiver']}: {row['message']}")
            send_to(client_sock, '-----------------------------------')

        send_to(client_sock, 'Type /help for available commands.')

        with clients_lock:
            clients[client_sock] = {
                'username': username, 'ip': client_ip, 'port': addr[1],
                'login_time': login_time, 'last_active': time.time(),
            }
        with stats_lock:
            stats['total_connected'] += 1

        broadcast(f'*** {username} has joined the chat ***', exclude_sock=client_sock)

        # ── Message loop with per-recv exception handling (Task 1 & 2) ──
        while not shutdown_event.is_set():
            try:
                data = client_sock.recv(4096)
            except socket.timeout:
                # No data for SOCKET_RECV_TIMEOUT seconds — client may be
                # dead. inactivity sweeper double-checks; keep looping.
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                cleanup_client(client_sock, username=username,
                                reason=f'Connection reset: {e}', abnormal=True)
                return

            if not data:
                # Peer closed the socket cleanly (FIN received)
                cleanup_client(client_sock, username=username, reason='Client closed connection')
                return

            try:
                message = data.decode().strip()
            except UnicodeDecodeError:
                send_to(client_sock, '[ERROR] Malformed data received.')
                log_security('MALFORMED_INPUT', username, client_ip, 'Non-UTF8 payload')
                continue

            if not message:
                continue

            with clients_lock:
                if client_sock in clients:
                    clients[client_sock]['last_active'] = time.time()

            ok, err = validate_message(message)
            if not ok:
                send_to(client_sock, f'[ERROR] {err}')
                log_security('INVALID_INPUT', username, client_ip, err)
                continue

            with stats_lock:
                stats['messages_processed'] += 1

            if message == '/logout':
                send_to(client_sock, '[SERVER] You have been logged out.')
                log_security('LOGOUT', username, client_ip)
                cleanup_client(client_sock, username=username, reason='User logout')
                return

            elif message == '/list':
                send_to(client_sock, build_user_list())

            elif message == '/stats':
                send_to(client_sock, build_stats())

            elif message == '/help':
                send_to(client_sock, 'Commands: /list  /msg <user> <text>  /stats  /logout  /help')

            elif message.startswith('/msg '):
                parts = message.split(' ', 2)
                if len(parts) < 3:
                    send_to(client_sock, 'Usage: /msg <username> <message>')
                    continue
                target_name, private_msg = parts[1], parts[2]
                target_sock = find_socket(target_name)
                if target_sock is None:
                    send_to(client_sock, f'[ERROR] User "{target_name}" not found.')
                elif target_name == username:
                    send_to(client_sock, '[ERROR] Cannot send private message to yourself.')
                else:
                    send_to(target_sock, f'[PRIVATE from {username}] {private_msg}')
                    send_to(client_sock, f'[PRIVATE to {target_name}] {private_msg}')
                    log_chat(username, target_name, 'PRIVATE', private_msg)
                    with stats_lock:
                        stats['private_messages'] += 1

            else:
                formatted = f'[{username}] {message}'
                broadcast(formatted)
                log_chat(username, 'ALL', 'BROADCAST', message)
                with stats_lock:
                    stats['broadcast_messages'] += 1

    except Exception as e:
        # Last-resort catch: never let one client's bug kill a worker
        # thread silently. Log it with full context and release resources.
        log_security('UNHANDLED_ERROR', username or 'unknown', client_ip, str(e))
        cleanup_client(client_sock, username=username, reason=f'Unhandled error: {e}', abnormal=True)


# ── Task 2: graceful shutdown ───────────────────────────────────────────
def graceful_shutdown(signum=None, frame=None):
    if shutdown_event.is_set():
        return
    print('\n[SERVER] Shutdown signal received — closing connections gracefully...')
    shutdown_event.set()

    with clients_lock:
        active = list(clients.items())
    for sock, info in active:
        send_to(sock, '[SERVER] Server is shutting down. Goodbye!')
        cleanup_client(sock, username=info['username'], reason='Server shutdown')

    if server_socket:
        try:
            server_socket.close()
        except OSError:
            pass

    if executor:
        executor.shutdown(wait=True, cancel_futures=True)

    log_server('SERVER_SHUTDOWN', 'system', '-')
    print('[SERVER] Shutdown complete.')
    sys.exit(0)


# ── Main ────────────────────────────────────────────────────────────────
def main():
    global server_socket, executor

    init_files()
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    threading.Thread(target=check_timeouts, daemon=True).start()

    executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix='client')

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(LISTEN_BACKLOG)

    print(f'Secure chat server listening on port {PORT} ...')
    print(f'Config           : {CONFIG_PATH}')
    print(f'Thread pool size : {THREAD_POOL_SIZE}  (bounded — Task 3 scalability)')
    print(f'Users file       : {USERS_FILE}')
    print(f'Security log     : {SECURITY_LOG}')
    log_server('SERVER_START', 'system', '-', f'port={PORT}')

    try:
        while not shutdown_event.is_set():
            try:
                client_sock, addr = server_socket.accept()
            except OSError:
                break  # socket closed during shutdown

            with clients_lock:
                current = len(clients)
            if current >= MAX_CLIENTS:
                # Task 3: don't accept unbounded connections — fail fast
                # with a meaningful message instead of degrading silently.
                send_to(client_sock, 'AUTH_FAIL|Server is at capacity. Please try again shortly.')
                log_security('CONNECTION_REJECTED', 'unknown', addr[0], f'At capacity ({current}/{MAX_CLIENTS})')
                with stats_lock:
                    stats['connections_rejected'] += 1
                client_sock.close()
                continue

            executor.submit(handle_client, client_sock, addr)
    except KeyboardInterrupt:
        graceful_shutdown()
    finally:
        if not shutdown_event.is_set():
            graceful_shutdown()


if __name__ == '__main__':
    main()