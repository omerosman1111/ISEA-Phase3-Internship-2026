# Assignment 6: GUI-Based Multi-Client Chat Application Using TCP
## Overview

This assignment extends the Assignment 5 terminal-based TCP chat application by replacing
the terminal client with a graphical desktop application built using Python Tkinter.
The server is reused unchanged from Assignment 5.

---

## Files

| File | Description |
|---|---|
| `server.py` | Multi-client TCP chat server (reused from Assignment 5, no changes) |
| `client_gui.py` | GUI-based chat client built with Python Tkinter |
| `screenshots/` | All required screenshots from testing |
| `report.pdf` | Full assignment report |

---

## Requirements

- Python 3.x
- tkinter (included in standard Python installation)
- Mininet installed on Ubuntu/Linux

---

## Network Setup

Start Mininet with 5 hosts (1 server + 4 clients):

```bash
sudo mn --topo single,5
```

Topology:
- h1 = Chat Server (IP: 10.0.0.1)
- h2 = Client A
- h3 = Client B
- h4 = Client C
- h5 = Client D

Verify connectivity:
```
mininet> nodes
mininet> net
mininet> pingall
```

---

## How to Run

### Step 1 — Open xterm windows for all hosts
```
mininet> xterm h1 h2 h3 h4 h5
```

### Step 2 — Start the server on h1
```bash
python3 server.py
```
You should see:
```
Chat server listening on port 5000 ...
```

### Step 3 — Launch the GUI client on h2, h3, h4, h5
On each client xterm:
```bash
python3 client_gui.py
```

A login window will appear. Enter a username and click **Connect**.

---

## GUI Features

### Login Window
- Enter a username (no spaces, required)
- Click Connect or press Enter
- Shows connection status

### Chat Window
- **Status bar** — shows username, server IP, and Online/Offline status
- **Message area** — displays all received messages with auto-scroll
- **Online Users panel** — live list of connected users (double-click to private message)
- **Send button** — sends the typed message (or press Enter)
- **Disconnect button** — closes connection and returns to login window

---

## Available Commands

Type these in the message box and press Send:

| Command | Description |
|---|---|
| `/list` | Show all currently online users |
| `/msg <username> <text>` | Send a private message to a specific user |
| `/stats` | Show server statistics |
| `/help` | Show available commands |

 

## How Private Messaging Works

Type in the message box:
```
/msg clientB Hello, this is a private message
```
Only clientB will receive this message. Other users will not see it.

Alternatively, double-click a username in the Online Users panel to auto-fill `/msg <username>`.

 

## Log Files (generated automatically by the server)

| File | Contents |
|---|---|
| `server_log.txt` | Connection/disconnection events with timestamps |
| `chat_history.csv` | All messages (broadcast and private) with sender, receiver, type |

When a user reconnects with the same username, the server automatically shows their last 5 messages.

---

## Execution Commands Summary

```bash
# h1 (server):
python3 server.py

# h2, h3, h4, h5 (GUI clients):
python3 client_gui.py
 

 
