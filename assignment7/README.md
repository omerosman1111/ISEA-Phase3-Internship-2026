# Assignment 7: Secure Network Application Development Using TCP



## Overview

This assignment extends the Assignment 6 GUI chat application with practical security
mechanisms: user authentication, SHA-256 password hashing, duplicate login prevention,
input validation, failed login protection, and session management with inactivity timeout.

---

## Files

| File | Description |
|---|---|
| `server.py` | Secure multi-client TCP chat server |
| `client_gui.py` | Secure GUI chat client (Tkinter) |
| `users.csv` | User database — stores username + SHA-256 hashed password only |
| `security_log.txt` | Security events log (auto-generated) |
| `chat_history.csv` | Chat message history (auto-generated) |
| `server_log.txt` | Connection events log (auto-generated) |
| `screenshots/` | All required screenshots |
| `report.pdf` | Full assignment report |
| `handwritten_reflection.pdf` | Handwritten reflection answers (scanned) |

---

## Security Features

| Feature | Implementation |
|---|---|
| Authentication | Username + password required before joining chat |
| Password hashing | SHA-256 via `hashlib.sha256()` — plaintext never stored |
| Duplicate login | Same username cannot be logged in twice simultaneously |
| Input validation | Username (3-20 chars, alphanumeric), password (4+ chars), message (max 500 chars) |
| Failed login protection | 5 failed attempts → 60 second lockout |
| Session timeout | Clients inactive for 5 minutes are disconnected automatically |
| Secure logging | security_log.txt records all auth events — passwords never logged |
| Logout | `/logout` command cleanly ends the session |

---

## How to Run

### Step 1 — Start Mininet
```bash
sudo mn --topo single,5
```

### Step 2 — Open xterms
```
mininet> xterm h1 h2 h3 h4 h5
```

### Step 3 — Start the server on h1
```bash
python3 server.py
```

### Step 4 — Launch GUI clients on h2–h5
```bash
python3 client_gui.py
```

Enter username and password in the login window:
- **First login:** registers the user automatically
- **Return login:** authenticates against stored SHA-256 hash

---

## Default User

A default `admin` user is created automatically:
- Username: `admin`
- Password: `admin123`

---

## Available Commands

| Command | Description |
|---|---|
| `/list` | Show all online users |
| `/msg <username> <text>` | Send a private message |
| `/stats` | Show server statistics |
| `/logout` | Log out and return to login window |
| `/help` | Show available commands |

---

## Security Notes

- Passwords are **never** stored in plaintext — only SHA-256 hashes in `users.csv`
- Passwords are **never** printed to console or written to any log file
- Password field in the GUI is masked with `*`
- After authentication, the password variable is cleared from memory immediately
- The lockout mechanism prevents brute-force attacks (5 attempts → 60s block)
