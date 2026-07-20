# ISEA-Phase3-TezpurUniversity-SecureChatApp

Secure, multi-client TCP chat application built for the ISEA Phase III Networking
Internship, Department of Computer Science & Engineering, Tezpur University.

Developed incrementally across three assignments:

| Assignment | Focus |
|---|---|
| 6 | GUI-based multi-client TCP chat application |
| 7 | Application security — authentication, SHA-256 password hashing, duplicate-login prevention, input validation, failed-login lockout, session timeout, secure logging |
| 8 | **(this repo, current state)** Optimization — scalability, reliability, configuration management, performance evaluation |

---

## Features

**Security (Assignment 7)**
- Username/password authentication over a custom TCP protocol
- SHA-256 password hashing — plaintext passwords never stored or logged
- Duplicate-login prevention
- Input validation (usernames, passwords, messages, commands)
- Failed-login lockout (5 attempts → 60s lockout per IP)
- Session timeout, logout, and password-free security logging

**Optimization (Assignment 8)**
- Bounded `ThreadPoolExecutor` + connection-capacity limit — scales to 10+ concurrent clients without crashing
- Automatic dead-connection detection, idempotent resource cleanup, meaningful error messages
- Graceful server shutdown (`SIGINT`/`SIGTERM`) and automatic client-side reconnection with exponential backoff
- All tunables externalized to `config.json`
- Headless load-testing tool (`load_test.py`) + graph generator for before/after performance comparison

---

## Repository Structure

```
.
├── server.py                  # Optimized server (Assignment 8)
├── client_gui.py               # Optimized Tkinter client (Assignment 8)
├── config.json                 # All server/client configuration
├── load_test.py                # Headless concurrent-client benchmark tool
├── generate_graphs.py          # Builds before/after comparison graphs from performance_results.csv
├── performance_results.csv     # Benchmark results (5/8/10 clients, before & after)
├── graphs/                     # latency, throughput, CPU, memory comparison charts
├── screenshots/                # Evidence screenshots for every task
├── users.csv                   # username,password_hash (generated at runtime)
├── security_log.txt            # Auth/session audit trail (generated at runtime)
├── server_log.txt              # Connection lifecycle log (generated at runtime)
├── chat_history.csv            # Per-user message history (generated at runtime)
├── report.pdf                  # Assignment 8 report
└── handwritten_reflection.pdf  # Scanned reflection answers
```

---

## Requirements

- Python 3.8+
- [Mininet](http://mininet.org/) (network emulation)
- `psutil`, `matplotlib`, `pandas` for load testing / graphing:
  ```bash
  pip install psutil matplotlib pandas --break-system-packages
  ```

## Running

**1. Start the network topology (Mininet):**
```bash
sudo mn --topo single,11
nodes
net
pingall
```

**2. Start the server (on h1):**
```bash
xterm h1
python3 server.py
```

**3. Start clients (on any other host):**
```bash
xterm h2
python3 client_gui.py
```
New usernames are registered automatically on first login.

**4. Run a performance benchmark:**
```bash
python3 load_test.py --host 10.0.0.1 --port 5000 \
    --clients 10 --messages 60 --delay 0.3 \
    --label after --server-pid <server_PID>
```

**5. Generate comparison graphs:**
```bash
python3 generate_graphs.py performance_results.csv
```

## Configuration

All server and client parameters — host, port, thread pool size, timeouts, retry
policy, file paths — are defined in `config.json`. Missing or malformed config
falls back to safe built-in defaults, so a config typo never prevents the
server from starting.

## Wireshark Verification

Capture on the switch interface (`s1-eth1`) with filter `tcp.port == 5000` to
observe the authentication handshake, graceful shutdown, and automatic
reconnection at the packet level. See `report.pdf` Section 8 for annotated
captures.

## Known Issue (fixed)

Under concurrent logins (8+ clients), the original implementation had a race
condition where a client could receive another client's join/leave broadcast
before its own `AUTH_OK` confirmation, causing authentication to fail. Fixed
by ensuring a client's own confirmation is always sent before it is registered
as a broadcast target — see `report.pdf` Section 9 for the full writeup.

---

**Author:** Omer Osman
