#!/usr/bin/env python3
 
import socket
import threading
import datetime
import csv
import os
import hashlib
import json
import time

HOST            = '0.0.0.0'
PORT            = 5000
USERS_FILE      = 'users.csv'
SECURITY_LOG    = 'security_log.txt'
CHAT_HISTORY    = 'chat_history.csv'
SERVER_LOG      = 'server_log.txt'
INACTIVITY_TIMEOUT = 300   # 5 minutes in seconds
MAX_MSG_SIZE    = 500      # characters
MAX_FAIL_ATTEMPTS = 5
LOCKOUT_SECONDS = 60       # 1 minute lockout

# ── Shared state 
clients       = {}     # socket -> {username, ip, port, login_time, last_active}
clients_lock  = threading.Lock()

logged_in     = set()  # usernames currently logged in (duplicate prevention)
logged_in_lock = threading.Lock()

failed_attempts = {}   # ip -> {count, lockout_until}
fail_lock       = threading.Lock()

stats = {'total_connected':0, 'messages_processed':0,
         'broadcast_messages':0, 'private_messages':0}
stats_lock = threading.Lock()

log_lock = threading.Lock()


# ── Initialise files  
def init_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['username', 'password_hash'])
        # Create one default admin user: admin / admin123
        register_user('admin', 'admin123')

    if not os.path.exists(CHAT_HISTORY):
        with open(CHAT_HISTORY, 'w', newline='') as f:
            csv.writer(f).writerow(
                ['timestamp','sender','receiver','message_type','message'])


# ── Password hashing  
def hash_password(password):
    """SHA-256 hash — never store plaintext."""
    return hashlib.sha256(password.encode()).hexdigest()


# ── User management  
def register_user(username, password):
    """Register a new user with hashed password."""
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


# ── Input validation  
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
            return False, f'Unknown command. Type /help for available commands.'
    return True, 'OK'


# ── Lockout management  
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
            return True   # locked out now
    return False


def reset_failed_attempts(ip):
    with fail_lock:
        if ip in failed_attempts:
            failed_attempts[ip] = {'count': 0, 'lockout_until': None}


# ── Logging  
def ts():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_security(event, username, ip, detail=''):
    """Security log — never logs passwords."""
    line = f"{ts()} | {event:25s} | {username:20s} | {ip:15s} | {detail}\n"
    with log_lock:
        with open(SECURITY_LOG, 'a') as f:
            f.write(line)
    print(line.strip())


def log_server(event, username, ip):
    line = f"{ts()},{event},{username},{ip}\n"
    with log_lock:
        with open(SERVER_LOG, 'a') as f:
            f.write(line)


def log_chat(sender, receiver, msg_type, message):
    with log_lock:
        with open(CHAT_HISTORY, 'a', newline='') as f:
            csv.writer(f).writerow([ts(), sender, receiver, msg_type, message])


# ── Messaging helpers  
def send_to(sock, msg):
    try:
        sock.sendall((msg + '\n').encode())
    except Exception:
        pass


def broadcast(message, exclude_sock=None):
    with clients_lock:
        for s in list(clients.keys()):
            if s != exclude_sock:
                send_to(s, message)


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
            f"Currently online : {online}\n"
            f"Total connected  : {s['total_connected']}\n"
            f"Messages processed: {s['messages_processed']}\n"
            f"Broadcast messages: {s['broadcast_messages']}\n"
            f"Private messages  : {s['private_messages']}\n"
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


# ── Inactivity timeout checker  
def check_timeouts():
    """Runs in background — kicks inactive clients."""
    while True:
        time.sleep(30)
        now = time.time()
        with clients_lock:
            to_kick = []
            for s, info in clients.items():
                if now - info.get('last_active', now) > INACTIVITY_TIMEOUT:
                    to_kick.append((s, info['username']))
        for s, uname in to_kick:
            send_to(s, '[SERVER] You have been disconnected due to inactivity.')
            log_security('SESSION_TIMEOUT', uname, '', 'Inactivity timeout')
            try:
                s.close()
            except Exception:
                pass


# ── Per-client thread  
def handle_client(client_sock, addr):
    client_ip = addr[0]
    username  = None

    try:
        # ── Step 1: check lockout  
        lockout = is_locked_out(client_ip)
        if lockout:
            _, remaining = lockout
            send_to(client_sock,
                    f'AUTH_FAIL|Too many failed attempts. '
                    f'Try again in {remaining} seconds.')
            log_security('LOCKOUT_BLOCKED', 'unknown', client_ip,
                         f'{remaining}s remaining')
            client_sock.close()
            return

        # ── Step 2: receive credentials  
        send_to(client_sock, 'AUTH_REQUEST|Enter username:')
        raw_un = client_sock.recv(1024).decode().strip()

        send_to(client_sock, 'AUTH_REQUEST|Enter password:')
        raw_pw = client_sock.recv(1024).decode().strip()

        # ── Step 3: validate input  
        ok, msg = validate_username(raw_un)
        if not ok:
            send_to(client_sock, f'AUTH_FAIL|{msg}')
            log_security('INVALID_USERNAME', raw_un, client_ip, msg)
            client_sock.close()
            return

        ok, msg = validate_password(raw_pw)
        if not ok:
            send_to(client_sock, f'AUTH_FAIL|{msg}')
            log_security('INVALID_PASSWORD', raw_un, client_ip, msg)
            client_sock.close()
            return

        # ── Step 4: register new user OR authenticate 
        if not user_exists(raw_un):
            # New user — register automatically
            register_user(raw_un, raw_pw)
            log_security('USER_REGISTERED', raw_un, client_ip)
        else:
            # Existing user — verify password
            if not verify_user(raw_un, raw_pw):
                locked = record_failed_attempt(client_ip)
                log_security('AUTH_FAILED', raw_un, client_ip,
                             'Wrong password')
                if locked:
                    send_to(client_sock,
                            f'AUTH_FAIL|Too many failed attempts. '
                            f'Locked for {LOCKOUT_SECONDS} seconds.')
                else:
                    with fail_lock:
                        count = failed_attempts.get(client_ip, {}).get('count', 0)
                    remaining = MAX_FAIL_ATTEMPTS - count
                    send_to(client_sock,
                            f'AUTH_FAIL|Wrong password. '
                            f'{remaining} attempt(s) remaining.')
                client_sock.close()
                return

        # ── Step 5: duplicate login check  
        with logged_in_lock:
            if raw_un in logged_in:
                send_to(client_sock,
                        'AUTH_FAIL|This user is already logged in.')
                log_security('DUPLICATE_LOGIN', raw_un, client_ip)
                client_sock.close()
                return
            logged_in.add(raw_un)

        reset_failed_attempts(client_ip)
        username = raw_un
        login_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with clients_lock:
            clients[client_sock] = {
                'username'  : username,
                'ip'        : client_ip,
                'port'      : addr[1],
                'login_time': login_time,
                'last_active': time.time(),
            }

        with stats_lock:
            stats['total_connected'] += 1

        log_security('AUTH_SUCCESS', username, client_ip)
        log_server('CONNECTED', username, client_ip)
        send_to(client_sock, f'AUTH_OK|Welcome, {username}!')

        # Reconnect history
        history = get_last_messages(username, 5)
        if history:
            send_to(client_sock, f'--- Your last {len(history)} messages ---')
            for row in history:
                send_to(client_sock,
                        f"[{row['timestamp']}] [{row['message_type']}] "
                        f"to {row['receiver']}: {row['message']}")
            send_to(client_sock, '-----------------------------------')

        # Notify others
        broadcast(f'*** {username} has joined the chat ***',
                  exclude_sock=client_sock)
        send_to(client_sock, 'Type /help for available commands.')

        # ── Message loop  
        while True:
            data = client_sock.recv(4096)
            if not data:
                break

            message = data.decode().strip()
            if not message:
                continue

            # Update last active time
            with clients_lock:
                if client_sock in clients:
                    clients[client_sock]['last_active'] = time.time()

            # Validate message
            ok, err = validate_message(message)
            if not ok:
                send_to(client_sock, f'[ERROR] {err}')
                log_security('INVALID_INPUT', username, client_ip, err)
                continue

            with stats_lock:
                stats['messages_processed'] += 1

            # ── /logout  
            if message == '/logout':
                send_to(client_sock, '[SERVER] You have been logged out.')
                log_security('LOGOUT', username, client_ip)
                break

            # ── /list  
            elif message == '/list':
                send_to(client_sock, build_user_list())

            # ── /stats  
            elif message == '/stats':
                send_to(client_sock, build_stats())

            # ── /help  
            elif message == '/help':
                send_to(client_sock,
                        'Commands: /list  /msg <user> <text>  '
                        '/stats  /logout  /help')

            # ── /msg private  
            elif message.startswith('/msg '):
                parts = message.split(' ', 2)
                if len(parts) < 3:
                    send_to(client_sock, 'Usage: /msg <username> <message>')
                    continue
                target_name, private_msg = parts[1], parts[2]
                target_sock = find_socket(target_name)
                if target_sock is None:
                    send_to(client_sock,
                            f'[ERROR] User "{target_name}" not found.')
                elif target_name == username:
                    send_to(client_sock,
                            '[ERROR] Cannot send private message to yourself.')
                else:
                    send_to(target_sock,
                            f'[PRIVATE from {username}] {private_msg}')
                    send_to(client_sock,
                            f'[PRIVATE to {target_name}] {private_msg}')
                    log_chat(username, target_name, 'PRIVATE', private_msg)
                    with stats_lock:
                        stats['private_messages'] += 1

            # ── Broadcast  
            else:
                formatted = f'[{username}] {message}'
                broadcast(formatted)
                log_chat(username, 'ALL', 'BROADCAST', message)
                with stats_lock:
                    stats['broadcast_messages'] += 1

    except Exception as e:
        print(f'Error handling {username}: {e}')

    finally:
        with clients_lock:
            if client_sock in clients:
                del clients[client_sock]
        with logged_in_lock:
            if username and username in logged_in:
                logged_in.remove(username)
        if username:
            log_server('DISCONNECTED', username, client_ip)
            broadcast(f'*** {username} has left the chat ***')
        client_sock.close()


# ── Main  
def main():
    init_files()

    # Start inactivity timeout checker
    threading.Thread(target=check_timeouts, daemon=True).start()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)
    print(f'Secure chat server listening on port {PORT} ...')
    print(f'Users file  : {USERS_FILE}')
    print(f'Security log: {SECURITY_LOG}')

    try:
        while True:
            client_sock, addr = server_sock.accept()
            threading.Thread(
                target=handle_client,
                args=(client_sock, addr),
                daemon=True).start()
    except KeyboardInterrupt:
        print('\nServer shutting down...')
    finally:
        server_sock.close()


if __name__ == '__main__':
    main()
