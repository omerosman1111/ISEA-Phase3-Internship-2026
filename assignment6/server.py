#!/usr/bin/env python3
 

import socket
import threading
import datetime
import csv
import os

HOST = '0.0.0.0'
PORT = 5000

# ── Shared state  
# clients dict: socket -> { username, ip, port, login_time, status }
clients = {}
clients_lock = threading.Lock()

# Server-wide counters
stats = {
    'total_connected': 0,
    'messages_processed': 0,
    'broadcast_messages': 0,
    'private_messages': 0,
}
stats_lock = threading.Lock()

SERVER_LOG    = 'server_log.txt'
CHAT_HISTORY  = 'chat_history.csv'
log_lock      = threading.Lock()

# ── Initialise chat_history.csv  
if not os.path.exists(CHAT_HISTORY):
    with open(CHAT_HISTORY, 'w', newline='') as f:
        csv.writer(f).writerow(['timestamp','sender','receiver','message_type','message'])


# ── Helper functions  
def ts():
    return datetime.datetime.now().strftime('%H:%M:%S')


def log_server(event, username, client_ip):
    line = f"{ts()},{event},{username},{client_ip}\n"
    with log_lock:
        with open(SERVER_LOG, 'a') as f:
            f.write(line)
    print(line.strip())


def log_history(sender, receiver, msg_type, message):
    row = [ts(), sender, receiver, msg_type, message]
    with log_lock:
        with open(CHAT_HISTORY, 'a', newline='') as f:
            csv.writer(f).writerow(row)


def get_last_messages(username, n=5):
    """Return the last n messages sent BY username."""
    rows = []
    try:
        with open(CHAT_HISTORY, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['sender'] == username:
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows[-n:]


def broadcast(message, exclude_sock=None):
    """Send to all connected clients except (optionally) one."""
    with clients_lock:
        for sock in list(clients.keys()):
            if sock == exclude_sock:
                continue
            try:
                sock.sendall(message.encode())
            except:
                pass


def send_to(sock, message):
    try:
        sock.sendall(message.encode())
    except:
        pass


def find_client_by_username(username):
    """Return socket of a connected user by username, or None."""
    with clients_lock:
        for sock, info in clients.items():
            if info['username'] == username:
                return sock
    return None


def build_user_list():
    with clients_lock:
        names = [info['username'] for info in clients.values()]
    if not names:
        return 'No users currently online.\n'
    return 'Online users: ' + ', '.join(names) + '\n'


def build_stats():
    with stats_lock:
        s = stats.copy()
    with clients_lock:
        online = len(clients)
    return (
        f"=== Server Statistics ===\n"
        f"Currently online : {online}\n"
        f"Total connected  : {s['total_connected']}\n"
        f"Messages processed: {s['messages_processed']}\n"
        f"Broadcast messages: {s['broadcast_messages']}\n"
        f"Private messages  : {s['private_messages']}\n"
        f"=========================\n"
    )


# ── Per-client thread  
def handle_client(client_sock, addr):
    client_ip   = addr[0]
    client_port = addr[1]
    username    = None

    try:
        # ── Registration  
        send_to(client_sock, 'Enter Username: ')
        username = client_sock.recv(1024).decode().strip()
        if not username:
            client_sock.close()
            return

        login_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with clients_lock:
            clients[client_sock] = {
                'username'  : username,
                'ip'        : client_ip,
                'port'      : client_port,
                'login_time': login_time,
                'status'    : 'online',
            }

        with stats_lock:
            stats['total_connected'] += 1

        log_server('CONNECTED', username, client_ip)

        # ── Reconnect: show last 5 messages  
        history = get_last_messages(username, 5)
        if history:
            send_to(client_sock, f'\n--- Your last {len(history)} messages ---\n')
            for row in history:
                send_to(client_sock,
                        f"[{row['timestamp']}] [{row['message_type']}] "
                        f"to {row['receiver']}: {row['message']}\n")
            send_to(client_sock, '-----------------------------------\n')

        # ── Notify everyone that this user joined  
        join_msg = f'*** {username} has joined the chat ***\n'
        broadcast(join_msg, exclude_sock=client_sock)
        send_to(client_sock, f'Welcome, {username}! Type /help for commands.\n')

        # ── Message loop  
        while True:
            data = client_sock.recv(4096)
            if not data:
                break

            message = data.decode().strip()
            if not message:
                continue

            with stats_lock:
                stats['messages_processed'] += 1

            # ── /list command  
            if message == '/list':
                send_to(client_sock, build_user_list())

            # ── /stats command  
            elif message == '/stats':
                send_to(client_sock, build_stats())

            # ── /help command  
            elif message == '/help':
                help_text = (
                    'Commands:\n'
                    '  /list               - show online users\n'
                    '  /msg <user> <text>  - send private message\n'
                    '  /stats              - show server statistics\n'
                    '  /help               - show this help\n'
                    '  (anything else)     - broadcast to all\n'
                )
                send_to(client_sock, help_text)

            # ── /msg private messaging  
            elif message.startswith('/msg '):
                parts = message.split(' ', 2)
                if len(parts) < 3:
                    send_to(client_sock,
                            'Usage: /msg <username> <message>\n')
                    continue

                target_name = parts[1]
                private_msg = parts[2]
                target_sock = find_client_by_username(target_name)

                if target_sock is None:
                    send_to(client_sock,
                            f'Error: User "{target_name}" not found or not online.\n')
                elif target_name == username:
                    send_to(client_sock,
                            'Error: You cannot send a private message to yourself.\n')
                else:
                    # Deliver to recipient
                    send_to(target_sock,
                            f'[PRIVATE from {username}] {private_msg}\n')
                    # Confirm to sender
                    send_to(client_sock,
                            f'[PRIVATE to {target_name}] {private_msg}\n')

                    log_history(username, target_name, 'PRIVATE', private_msg)
                    with stats_lock:
                        stats['private_messages'] += 1

            # ── Broadcast message 
            else:
                formatted = f'[{username}] {message}\n'
                broadcast(formatted)
                log_history(username, 'ALL', 'BROADCAST', message)
                with stats_lock:
                    stats['broadcast_messages'] += 1

    except Exception as e:
        print(f'Error handling {username}: {e}')

    finally:
        with clients_lock:
            if client_sock in clients:
                del clients[client_sock]

        if username:
            log_server('DISCONNECTED', username, client_ip)
            leave_msg = f'*** {username} has left the chat ***\n'
            broadcast(leave_msg)

        client_sock.close()


# ── Main  
def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)
    print(f'Chat server listening on port {PORT} ...')

    try:
        while True:
            client_sock, addr = server_sock.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, addr),
                daemon=True)
            t.start()
    except KeyboardInterrupt:
        print('\nServer shutting down...')
    finally:
        server_sock.close()


if __name__ == '__main__':
    main()