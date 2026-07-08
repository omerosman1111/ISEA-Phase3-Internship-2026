#!/usr/bin/env python3
 

import socket
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox

SERVER_IP   = '10.0.0.1'
SERVER_PORT = 5000


# ── LOGIN WINDOW  
class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title('Chat Login')
        self.root.geometry('300x180')
        self.root.resizable(False, False)
        self.root.configure(bg='white')

        tk.Label(self.root, text='TCP Chat Application',
                 font=('Arial', 12, 'bold'), bg='white').pack(pady=(15, 10))

        frame = tk.Frame(self.root, bg='white')
        frame.pack()

        tk.Label(frame, text='Username:', bg='white',
                 font=('Arial', 10)).grid(row=0, column=0,
                 sticky='e', padx=5, pady=5)
        self.username_entry = tk.Entry(frame, font=('Arial', 10), width=16)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)
        self.username_entry.focus_set()

        self.status_label = tk.Label(self.root, text='',
                                     font=('Arial', 9), bg='white', fg='red')
        self.status_label.pack()

        tk.Button(self.root, text='Connect',
                  font=('Arial', 10), width=10,
                  command=self.connect).pack(pady=8)

        self.root.bind('<Return>', lambda e: self.connect())

    def connect(self):
        username = self.username_entry.get().strip()

        if not username:
            messagebox.showerror('Error', 'Please enter a username.')
            return
        if ' ' in username:
            messagebox.showerror('Error', 'Username cannot contain spaces.')
            return

        self.status_label.config(text='Connecting...', fg='blue')
        self.root.update()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((SERVER_IP, SERVER_PORT))
            sock.recv(1024)
            sock.sendall(username.encode())
            sock.settimeout(None)
        except Exception as e:
            self.status_label.config(text=f'Failed: {e}', fg='red')
            messagebox.showerror('Connection Error', str(e))
            return

        self.root.withdraw()
        ChatWindow(self.root, sock, username)


# ── CHAT WINDOW  
class ChatWindow:
    def __init__(self, root, sock, username):
        self.root     = root
        self.sock     = sock
        self.username = username
        self.running  = True

        self.win = tk.Toplevel(root)
        self.win.title(f'Chat - {username}')
        self.win.geometry('700x500')
        self.win.minsize(600, 420)        # prevent shrinking too small
        self.win.configure(bg='white')
        self.win.protocol('WM_DELETE_WINDOW', self.disconnect)

        self._build_ui()

        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _build_ui(self):
        # ── Use grid on the main window for full control  
        self.win.grid_rowconfigure(1, weight=1)   # main area expands
        self.win.grid_columnconfigure(0, weight=1)

        # ── Row 0: status bar  
        status_frame = tk.Frame(self.win, bg='#f0f0f0',
                                relief='groove', bd=1)
        status_frame.grid(row=0, column=0, sticky='ew', padx=5, pady=(5, 0))

        tk.Label(status_frame,
                 text=f'Connected as: {self.username}  |  Server: {SERVER_IP}',
                 font=('Arial', 9), bg='#f0f0f0').pack(side='left', padx=5, pady=3)

        self.status_label = tk.Label(status_frame, text='Status: Online',
                                     font=('Arial', 9), bg='#f0f0f0', fg='green')
        self.status_label.pack(side='right', padx=5)

        # ── Row 1: main area (messages + users)  
        main = tk.Frame(self.win, bg='white')
        main.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)   # chat expands
        main.grid_columnconfigure(1, minsize=150) # users fixed width

        # ── Messages area  
        chat_frame = tk.Frame(main, bg='white')
        chat_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        chat_frame.grid_rowconfigure(1, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)

        tk.Label(chat_frame, text='Messages',
                 font=('Arial', 10, 'bold'), bg='white').grid(
                 row=0, column=0, sticky='w')

        self.chat_area = scrolledtext.ScrolledText(
            chat_frame,
            state='disabled',
            font=('Arial', 10),
            bg='white', fg='black',
            wrap='word', relief='solid', bd=1)
        self.chat_area.grid(row=1, column=0, sticky='nsew')

        # ── Online users area 
        users_frame = tk.Frame(main, bg='white')
        users_frame.grid(row=0, column=1, sticky='nsew')
        users_frame.grid_rowconfigure(1, weight=1)
        users_frame.grid_columnconfigure(0, weight=1)

        tk.Label(users_frame, text='Online Users',
                 font=('Arial', 10, 'bold'), bg='white').grid(
                 row=0, column=0, pady=(0, 3))

        self.users_listbox = tk.Listbox(
            users_frame,
            font=('Arial', 10),
            bg='white', fg='black',
            relief='solid', bd=1,
            selectmode='single',
            activestyle='none',
            width=16)
        self.users_listbox.grid(row=1, column=0, sticky='nsew')
        self.users_listbox.bind('<Double-Button-1>', self._user_click)

        tk.Label(users_frame, text='Double-click to\nprivate message',
                 font=('Arial', 8), bg='white', fg='gray').grid(
                 row=2, column=0, pady=3)

        # ── Row 2: input row  
        input_frame = tk.Frame(self.win, bg='white')
        input_frame.grid(row=2, column=0, sticky='ew', padx=5, pady=(0, 3))
        input_frame.grid_columnconfigure(0, weight=1)  # entry expands

        self.msg_entry = tk.Entry(input_frame,
                                  font=('Arial', 10),
                                  relief='solid', bd=1)
        self.msg_entry.grid(row=0, column=0, sticky='ew', ipady=5)
        self.msg_entry.bind('<Return>', lambda e: self._send())
        self.msg_entry.focus_set()

        tk.Button(input_frame, text='Send',
                  font=('Arial', 10), width=8,
                  command=self._send).grid(row=0, column=1, padx=(5, 0))

        tk.Button(input_frame, text='Disconnect',
                  font=('Arial', 10), width=11,
                  command=self.disconnect).grid(row=0, column=2, padx=(5, 0))

        # ── Row 3: hint 
        tk.Label(self.win,
                 text='Commands: /list   /msg <username> <text>   /stats   /help',
                 font=('Arial', 8), bg='white', fg='gray').grid(
                 row=3, column=0, sticky='w', padx=5, pady=(0, 4))

    # ── Append to chat  
    def _append(self, text):
        self.chat_area.config(state='normal')
        self.chat_area.insert('end', text + '\n')
        self.chat_area.config(state='disabled')
        self.chat_area.see('end')

    # ── Update users listbox  
    def _update_users(self, line):
        if line.startswith('Online users:'):
            names_str = line.replace('Online users:', '').strip()
            names = [n.strip() for n in names_str.split(',') if n.strip()]
            self.users_listbox.delete(0, 'end')
            for name in names:
                self.users_listbox.insert('end', name)
        elif line.startswith('No users currently'):
            self.users_listbox.delete(0, 'end')

    # ── Double-click user  
    def _user_click(self, event):
        sel = self.users_listbox.curselection()
        if not sel:
            return
        name = self.users_listbox.get(sel[0]).strip()
        if name and name != self.username:
            self.msg_entry.delete(0, 'end')
            self.msg_entry.insert(0, f'/msg {name} ')
            self.msg_entry.focus_set()

    # ── Send  
    def _send(self):
        msg = self.msg_entry.get().strip()
        if not msg or not self.running:
            return
        try:
            self.sock.sendall(msg.encode())
        except Exception as e:
            self._append(f'[Error] Could not send: {e}')
            return
        self.msg_entry.delete(0, 'end')

    # ── Background receive loop 
    def _receive_loop(self):
        buf = ''
        while self.running:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                buf += data.decode()
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.strip()
                    if line:
                        self.win.after(0, self._handle_line, line)
            except Exception:
                break
        if self.running:
            self.win.after(0, self._on_disconnected)

    def _handle_line(self, line):
        if not self.running:
            return
        self._update_users(line)
        self._append(line)
        if line.startswith('***'):
            self.win.after(200, self._refresh_list)

    def _refresh_list(self):
        if self.running:
            try:
                self.sock.sendall('/list'.encode())
            except Exception:
                pass

    def _on_disconnected(self):
        self.running = False
        self.status_label.config(text='Status: Offline', fg='red')
        self._append('[System] Disconnected from server.')

    # ── Disconnect 
    def disconnect(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass
        self.win.destroy()
        self.root.deiconify()


# ── ENTRY POINT  
if __name__ == '__main__':
    root = tk.Tk()
    LoginWindow(root)
    root.mainloop()