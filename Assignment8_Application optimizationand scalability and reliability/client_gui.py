#!/usr/bin/env python3
import socket
import threading
import time
import json
import os
import tkinter as tk
from tkinter import scrolledtext, messagebox

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

DEFAULT_CLIENT_CONFIG = {
    "server_ip": "10.0.0.1", "server_port": 5000,
    "connect_timeout": 8, "socket_timeout": 15,
    "reconnect_attempts": 5, "reconnect_base_delay": 2,
    "reconnect_max_delay": 20,
}


def load_client_config():
    if not os.path.exists(CONFIG_PATH):
        print(f'[CONFIG] {CONFIG_PATH} not found — using built-in defaults.')
        return DEFAULT_CLIENT_CONFIG
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
        return {**DEFAULT_CLIENT_CONFIG, **cfg.get('client', {})}
    except (json.JSONDecodeError, OSError) as e:
        print(f'[CONFIG] Failed to parse {CONFIG_PATH} ({e}) — using defaults.')
        return DEFAULT_CLIENT_CONFIG


CFG = load_client_config()
SERVER_IP             = CFG['server_ip']
SERVER_PORT           = CFG['server_port']
CONNECT_TIMEOUT       = CFG['connect_timeout']
SOCKET_TIMEOUT        = CFG['socket_timeout']
RECONNECT_ATTEMPTS    = CFG['reconnect_attempts']
RECONNECT_BASE_DELAY  = CFG['reconnect_base_delay']
RECONNECT_MAX_DELAY   = CFG['reconnect_max_delay']


# ── LOGIN WINDOW ─────────────────────────────────────────────────────
class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title('Secure Chat Login')
        self.root.geometry('320x230')
        self.root.resizable(False, False)
        self.root.configure(bg='white')

        tk.Label(self.root, text='Secure TCP Chat Application',
                 font=('Arial', 12, 'bold'), bg='white').pack(pady=(15, 5))
        tk.Label(self.root, text='New users are registered automatically.',
                 font=('Arial', 8), bg='white', fg='gray').pack()

        frame = tk.Frame(self.root, bg='white')
        frame.pack(pady=10)

        tk.Label(frame, text='Username:', bg='white',
                 font=('Arial', 10)).grid(row=0, column=0, sticky='e', padx=5, pady=5)
        self.username_entry = tk.Entry(frame, font=('Arial', 10), width=16)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)
        self.username_entry.focus_set()

        tk.Label(frame, text='Password:', bg='white',
                 font=('Arial', 10)).grid(row=1, column=0, sticky='e', padx=5, pady=5)
        self.password_entry = tk.Entry(frame, font=('Arial', 10), width=16, show='*')
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)

        self.status_label = tk.Label(self.root, text='', font=('Arial', 9),
                                      bg='white', fg='red', wraplength=280)
        self.status_label.pack()

        tk.Button(self.root, text='Connect', font=('Arial', 10), width=10,
                  command=self.connect).pack(pady=6)

        self.root.bind('<Return>', lambda e: self.connect())

    def _set_status(self, text, color='red'):
        self.status_label.config(text=text, fg=color)
        self.root.update()

    def connect(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username:
            messagebox.showerror('Error', 'Please enter a username.')
            return
        if not password:
            messagebox.showerror('Error', 'Please enter a password.')
            return
        if len(username) < 3:
            messagebox.showerror('Error', 'Username must be at least 3 characters.')
            return
        if ' ' in username:
            messagebox.showerror('Error', 'Username cannot contain spaces.')
            return

        self._set_status('Connecting...', 'blue')

        sock, err = self._try_connect_and_auth(username, password)
        if err:
            self._set_status(err, 'red')
            self.password_entry.delete(0, 'end')
            return

        self.password_entry.delete(0, 'end')
        self.root.withdraw()
        # Password is kept only in memory, only for silent auto-reconnect,
        # and only for the lifetime of the session (see ChatWindow).
        ChatWindow(self.root, sock, username, password)

    def _try_connect_and_auth(self, username, password):
        """Returns (socket, None) on success or (None, error_message)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((SERVER_IP, SERVER_PORT))
        except socket.timeout:
            return None, f'Failed: connection to {SERVER_IP}:{SERVER_PORT} timed out.'
        except ConnectionRefusedError:
            return None, f'Failed: server at {SERVER_IP}:{SERVER_PORT} refused the connection (is it running?).'
        except OSError as e:
            return None, f'Failed: network error ({e}).'

        try:
            resp = sock.recv(1024).decode().strip()
            if not resp.startswith('AUTH_REQUEST'):
                raise Exception('Unexpected server response.')
            sock.sendall(username.encode())

            resp = sock.recv(1024).decode().strip()
            if not resp.startswith('AUTH_REQUEST'):
                raise Exception('Unexpected server response.')
            sock.sendall(password.encode())

            resp = sock.recv(2048).decode().strip()

            if resp.startswith('AUTH_FAIL'):
                reason = resp.split('|', 1)[1] if '|' in resp else resp
                sock.close()
                return None, reason

            if not resp.startswith('AUTH_OK'):
                raise Exception('Unexpected server response.')

            sock.settimeout(SOCKET_TIMEOUT)
            return sock, None

        except socket.timeout:
            sock.close()
            return None, 'Failed: server did not respond in time during login.'
        except (ConnectionResetError, OSError) as e:
            sock.close()
            return None, f'Failed: connection lost during login ({e}).'
        except Exception as e:
            sock.close()
            return None, f'Failed: {e}'


# ── CHAT WINDOW ──────────────────────────────────────────────────────
class ChatWindow:
    def __init__(self, root, sock, username, password):
        self.root       = root
        self.sock       = sock
        self.username   = username
        self._password  = password   # kept in memory only, for auto-reconnect
        self.running    = True
        self.user_closed = False     # True only when the user explicitly disconnects
        self.reconnecting = False

        self.win = tk.Toplevel(root)
        self.win.title(f'Secure Chat - {username}')
        self.win.geometry('700x520')
        self.win.minsize(600, 440)
        self.win.configure(bg='white')
        self.win.protocol('WM_DELETE_WINDOW', self.disconnect)

        self._build_ui()
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _build_ui(self):
        self.win.grid_rowconfigure(1, weight=1)
        self.win.grid_columnconfigure(0, weight=1)

        status_frame = tk.Frame(self.win, bg='#f0f0f0', relief='groove', bd=1)
        status_frame.grid(row=0, column=0, sticky='ew', padx=5, pady=(5, 0))

        tk.Label(status_frame,
                 text=f'Logged in as: {self.username}  |  Server: {SERVER_IP}  |  \U0001F512 Authenticated',
                 font=('Arial', 9), bg='#f0f0f0').pack(side='left', padx=5, pady=3)

        self.status_label = tk.Label(status_frame, text='Status: Online',
                                      font=('Arial', 9), bg='#f0f0f0', fg='green')
        self.status_label.pack(side='right', padx=5)

        main = tk.Frame(self.win, bg='white')
        main.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, minsize=150)

        chat_frame = tk.Frame(main, bg='white')
        chat_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        chat_frame.grid_rowconfigure(1, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)

        tk.Label(chat_frame, text='Messages', font=('Arial', 10, 'bold'),
                  bg='white').grid(row=0, column=0, sticky='w')

        self.chat_area = scrolledtext.ScrolledText(
            chat_frame, state='disabled', font=('Arial', 10),
            bg='white', fg='black', wrap='word', relief='solid', bd=1)
        self.chat_area.grid(row=1, column=0, sticky='nsew')

        users_frame = tk.Frame(main, bg='white')
        users_frame.grid(row=0, column=1, sticky='nsew')
        users_frame.grid_rowconfigure(1, weight=1)
        users_frame.grid_columnconfigure(0, weight=1)

        tk.Label(users_frame, text='Online Users', font=('Arial', 10, 'bold'),
                  bg='white').grid(row=0, column=0, pady=(0, 3))

        self.users_listbox = tk.Listbox(
            users_frame, font=('Arial', 10), bg='white', fg='black',
            relief='solid', bd=1, selectmode='single', activestyle='none', width=16)
        self.users_listbox.grid(row=1, column=0, sticky='nsew')
        self.users_listbox.bind('<Double-Button-1>', self._user_click)

        tk.Label(users_frame, text='Double-click to\nprivate message',
                  font=('Arial', 8), bg='white', fg='gray').grid(row=2, column=0, pady=3)

        input_frame = tk.Frame(self.win, bg='white')
        input_frame.grid(row=2, column=0, sticky='ew', padx=5, pady=(0, 3))
        input_frame.grid_columnconfigure(0, weight=1)

        self.msg_entry = tk.Entry(input_frame, font=('Arial', 10), relief='solid', bd=1)
        self.msg_entry.grid(row=0, column=0, sticky='ew', ipady=5)
        self.msg_entry.bind('<Return>', lambda e: self._send())
        self.msg_entry.focus_set()

        tk.Button(input_frame, text='Send', font=('Arial', 10), width=8,
                  command=self._send).grid(row=0, column=1, padx=(5, 0))
        tk.Button(input_frame, text='Logout', font=('Arial', 10), width=8,
                  command=self.logout).grid(row=0, column=2, padx=(5, 0))
        tk.Button(input_frame, text='Disconnect', font=('Arial', 10), width=10,
                  command=self.disconnect).grid(row=0, column=3, padx=(5, 0))

        tk.Label(self.win,
                 text='Commands: /list   /msg <username> <text>   /stats   /logout   /help',
                 font=('Arial', 8), bg='white', fg='gray').grid(
                 row=3, column=0, sticky='w', padx=5, pady=(0, 4))

    # ── chat area helpers ──────────────────────────────────────────
    def _append(self, text):
        try:
            self.chat_area.config(state='normal')
            self.chat_area.insert('end', text + '\n')
            self.chat_area.config(state='disabled')
            self.chat_area.see('end')
        except tk.TclError:
            pass

    def _update_users(self, line):
        if line.startswith('Online users:'):
            names_str = line.replace('Online users:', '').strip()
            names = [n.strip() for n in names_str.split(',') if n.strip()]
            self.users_listbox.delete(0, 'end')
            for name in names:
                self.users_listbox.insert('end', name)
        elif line.startswith('No users'):
            self.users_listbox.delete(0, 'end')

    def _user_click(self, event):
        sel = self.users_listbox.curselection()
        if not sel:
            return
        name = self.users_listbox.get(sel[0]).strip()
        if name and name != self.username:
            self.msg_entry.delete(0, 'end')
            self.msg_entry.insert(0, f'/msg {name} ')
            self.msg_entry.focus_set()

    def _send(self):
        msg = self.msg_entry.get().strip()
        if not msg or not self.running:
            return
        if len(msg) > 500:
            self._append('[Error] Message too long (max 500 characters).')
            return
        try:
            self.sock.sendall(msg.encode())
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._append(f'[Error] Could not send: {e}')
            return
        self.msg_entry.delete(0, 'end')

    # ── receive loop with reconnection (Task 2) ────────────────────
    def _receive_loop(self):
        while self.running:
            buf = ''
            try:
                while self.running:
                    data = self.sock.recv(4096)
                    if not data:
                        break
                    buf += data.decode()
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        line = line.strip()
                        if line:
                            # Server-initiated shutdown/logout must be marked
                            # RIGHT HERE, synchronously, before this thread
                            # continues — otherwise the socket can close and
                            # this loop can start auto-reconnecting before the
                            # (delayed, main-thread) disconnect() call has run,
                            # racing reconnect logic against a closing window.
                            if 'shutting down' in line or '[SERVER] You have been' in line:
                                self.user_closed = True
                            self.win.after(0, self._handle_line, line)
            except socket.timeout:
                # No traffic for SOCKET_TIMEOUT seconds is not itself an
                # error for an idle chat session; loop back and keep
                # listening rather than treating it as a dead connection.
                if self.running:
                    continue
            except (ConnectionResetError, OSError):
                pass  # fall through to reconnect logic below

            if not self.running or self.user_closed:
                break

            # Connection dropped unexpectedly — attempt to reconnect.
            self.win.after(0, self._on_connection_lost)
            if self._attempt_reconnect():
                continue   # resume receiving on the new socket
            else:
                self.win.after(0, self._on_reconnect_failed)
                break

    def _win_alive(self):
        """True only if the Toplevel and its widgets still exist. Reconnect
        attempts run on a background thread and schedule UI updates with
        win.after(); if the user (or a server shutdown) has since closed
        the window, those updates must be silently skipped instead of
        raising _tkinter.TclError."""
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _on_connection_lost(self):
        if not self._win_alive():
            return
        self.status_label.config(text='Status: Reconnecting...', fg='orange')
        self._append('[System] Connection lost. Attempting to reconnect...')

    def _attempt_reconnect(self):
        self.reconnecting = True
        delay = RECONNECT_BASE_DELAY
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            if self.user_closed:
                return False
            self.win.after(0, self._append,
                            f'[System] Reconnect attempt {attempt}/{RECONNECT_ATTEMPTS}...')
            try:
                new_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                new_sock.settimeout(CONNECT_TIMEOUT)
                new_sock.connect((SERVER_IP, SERVER_PORT))

                resp = new_sock.recv(1024).decode().strip()
                if resp.startswith('AUTH_REQUEST'):
                    new_sock.sendall(self.username.encode())
                    resp = new_sock.recv(1024).decode().strip()
                    if resp.startswith('AUTH_REQUEST'):
                        new_sock.sendall(self._password.encode())
                        resp = new_sock.recv(2048).decode().strip()
                        if resp.startswith('AUTH_OK'):
                            new_sock.settimeout(SOCKET_TIMEOUT)
                            self.sock = new_sock
                            self.reconnecting = False
                            self.win.after(0, self._on_reconnect_success)
                            return True
                        elif resp.startswith('AUTH_FAIL'):
                            reason = resp.split('|', 1)[1] if '|' in resp else resp
                            self.win.after(0, self._append, f'[System] Reconnect rejected: {reason}')
                new_sock.close()
            except (socket.timeout, ConnectionRefusedError, OSError):
                pass

            time.sleep(min(delay, RECONNECT_MAX_DELAY))
            delay *= 2   # exponential backoff

        self.reconnecting = False
        return False

    def _on_reconnect_success(self):
        if not self._win_alive():
            return
        self.status_label.config(text='Status: Online', fg='green')
        self._append('[System] Reconnected successfully.')

    def _on_reconnect_failed(self):
        self.running = False
        if not self._win_alive():
            return
        self.status_label.config(text='Status: Offline', fg='red')
        self._append(f'[System] Could not reconnect after {RECONNECT_ATTEMPTS} attempts. '
                      'Please check the server and reconnect manually.')
        messagebox.showwarning('Disconnected',
            'Lost connection to the server and automatic reconnection failed.\n'
            'Please close this window and log in again.')

    def _handle_line(self, line):
        if not self.running:
            return
        if line.startswith('AUTH_OK|'):
            line = line.split('|', 1)[1]
        self._update_users(line)
        self._append(line)
        if line.startswith('***'):
            self.win.after(200, self._refresh_list)
        if '[SERVER] You have been' in line or 'shutting down' in line:
            # Give the user time to actually read/screenshot the server's
            # final message before the window auto-returns to login.
            self.win.after(4000, self.disconnect)

    def _run_login_ui_cleanup(self):
        if hasattr(self.root, 'winfo_children'):
            for widget in self.root.winfo_children():
                if isinstance(widget, tk.Label) and widget.cget('fg') == 'blue':
                    widget.config(text='')
                if isinstance(widget, tk.Frame):
                    for child in widget.winfo_children():
                        if isinstance(child, tk.Entry) and child.cget('show') == '*':
                            child.delete(0, 'end')

    def _refresh_list(self):
        if self.running:
            try:
                self.sock.sendall('/list'.encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def logout(self):
        """Explicit logout — no reconnection should follow."""
        self.user_closed = True
        if self.running:
            try:
                self.sock.sendall('/logout'.encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        self.win.after(300, self.disconnect)

    def disconnect(self):
        """Graceful shutdown of this session (Task 2)."""
        self.user_closed = True
        self.running = False
        self._password = None  # scrub reconnect credential from memory
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self._run_login_ui_cleanup()
        self.root.deiconify()


# ── ENTRY POINT  
if __name__ == '__main__':
    root = tk.Tk()
    LoginWindow(root)
    root.mainloop()