#!/usr/bin/env python3
"""
Assignment 8 — Task 5: Performance Evaluation.

Headless benchmark tool. Spins up N simulated concurrent clients against
the chat server, has each one authenticate and exchange messages, and
measures:

  - connection + auth latency
  - message round-trip latency (the server echoes broadcast messages
    back to their own sender, so send->receive time is a true RTT)
  - throughput (messages/sec across all simulated clients)
  - server-side CPU% and memory (RSS) sampled via psutil, if --server-pid
    is given (run this script ON THE SAME HOST as the server, or use
    `ps`/`top` on the server host and pass its PID)

Results are appended as one row per run to performance_results.csv, so
you can run this against the ORIGINAL Assignment 7 server (label
"before") and the ASSIGNMENT 8 optimized server (label "after") and get
a direct before/after comparison for the report.

USAGE
-----
  # On the server host, note the PID after starting server.py, e.g.:
  python3 server.py &
  echo $!            # -> server PID

  # Then, from a client host (or the same host):
  python3 load_test.py --host 10.0.0.1 --port 5000 --clients 10 \
      --messages 20 --label after --server-pid 1234

  # Repeat for 5, 8, 10 clients, and for both "before" and "after"
  # servers, to populate performance_results.csv for the report.
"""

import argparse
import csv
import os
import socket
import statistics
import threading
import time
import uuid
from datetime import datetime

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False


RESULTS_FILE = 'performance_results.csv'
FIELDNAMES = [
    'timestamp', 'label', 'num_clients', 'messages_per_client',
    'total_messages_sent', 'total_messages_confirmed',
    'connect_success', 'connect_failed',
    'avg_connect_latency_ms', 'avg_rtt_latency_ms', 'p95_rtt_latency_ms',
    'throughput_msgs_per_sec', 'duration_sec',
    'cpu_percent_avg', 'cpu_percent_peak',
    'mem_mb_avg', 'mem_mb_peak',
]


class SimClient:
    """One simulated headless chat client used purely for load testing."""

    def __init__(self, host, port, msg_count, delay):
        self.host = host
        self.port = port
        self.msg_count = msg_count
        self.delay = delay
        self.username = f'load_{uuid.uuid4().hex[:8]}'
        self.password = 'loadtest123'
        self.connect_latency = None
        self.rtts = []
        self.sent = 0
        self.confirmed = 0
        self.connected = False
        self.error = None
        self._sock = None
        self._send_times = {}
        self._lock = threading.Lock()

    def run(self):
        t0 = time.time()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self.host, self.port))

            resp = self._sock.recv(1024).decode().strip()
            if not resp.startswith('AUTH_REQUEST'):
                raise RuntimeError(f'unexpected: {resp}')
            self._sock.sendall(self.username.encode())

            resp = self._sock.recv(1024).decode().strip()
            if not resp.startswith('AUTH_REQUEST'):
                raise RuntimeError(f'unexpected: {resp}')
            self._sock.sendall(self.password.encode())

            resp = self._sock.recv(2048).decode().strip()
            if not resp.startswith('AUTH_OK'):
                raise RuntimeError(f'auth failed: {resp}')

            self.connect_latency = time.time() - t0
            self.connected = True
            self._sock.settimeout(5)

            reader = threading.Thread(target=self._reader, daemon=True)
            reader.start()

            for i in range(self.msg_count):
                text = f'ping-{i}-{uuid.uuid4().hex[:6]}'
                send_t = time.time()
                with self._lock:
                    self._send_times[text] = send_t
                try:
                    self._sock.sendall(text.encode())
                    self.sent += 1
                except OSError as e:
                    self.error = str(e)
                    break
                time.sleep(self.delay)

            time.sleep(2)  # grace period to receive trailing echoes
            try:
                self._sock.sendall('/logout'.encode())
            except OSError:
                pass
            time.sleep(0.3)

        except Exception as e:
            self.error = str(e)
        finally:
            try:
                self._sock.close()
            except Exception:
                pass

    def _reader(self):
        buf = ''
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                buf += data.decode(errors='ignore')
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    self._handle_line(line.strip())
        except OSError:
            pass

    def _handle_line(self, line):
        # Own broadcast messages come back as "[username] text"
        prefix = f'[{self.username}] '
        if line.startswith(prefix):
            text = line[len(prefix):]
            with self._lock:
                send_t = self._send_times.pop(text, None)
            if send_t is not None:
                self.rtts.append(time.time() - send_t)
                self.confirmed += 1


def sample_server_resources(pid, stop_event, samples):
    if not HAVE_PSUTIL:
        return
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        print(f'[WARN] No process with PID {pid} — skipping resource sampling.')
        return
    proc.cpu_percent(interval=None)  # prime the internal counter
    while not stop_event.is_set():
        try:
            cpu = proc.cpu_percent(interval=0.5)
            mem = proc.memory_info().rss / (1024 * 1024)
            samples.append((cpu, mem))
        except psutil.NoSuchProcess:
            break


def main():
    ap = argparse.ArgumentParser(description='Chat server load / performance test')
    ap.add_argument('--host', default='10.0.0.1')
    ap.add_argument('--port', type=int, default=5000)
    ap.add_argument('--clients', type=int, default=10, help='number of concurrent simulated clients')
    ap.add_argument('--messages', type=int, default=20, help='messages sent per client')
    ap.add_argument('--delay', type=float, default=0.2, help='delay between messages per client (sec)')
    ap.add_argument('--label', default='after', choices=['before', 'after'],
                     help='tag results as the pre- or post-optimization server')
    ap.add_argument('--server-pid', type=int, default=None,
                     help='PID of the server process, for CPU/memory sampling (run on the server host)')
    ap.add_argument('--output', default=RESULTS_FILE)
    args = ap.parse_args()

    print(f'--- Load test: {args.clients} clients x {args.messages} msgs '
          f'-> {args.host}:{args.port}  [{args.label}] ---')

    stop_event = threading.Event()
    samples = []
    sampler_thread = None
    if args.server_pid:
        if not HAVE_PSUTIL:
            print('[WARN] psutil not installed — run: pip install psutil --break-system-packages')
        else:
            sampler_thread = threading.Thread(
                target=sample_server_resources, args=(args.server_pid, stop_event, samples), daemon=True)
            sampler_thread.start()

    sim_clients = [SimClient(args.host, args.port, args.messages, args.delay) for _ in range(args.clients)]
    threads = [threading.Thread(target=c.run) for c in sim_clients]

    t_start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = time.time() - t_start

    stop_event.set()
    if sampler_thread:
        sampler_thread.join(timeout=2)

    ok = [c for c in sim_clients if c.connected]
    failed = [c for c in sim_clients if not c.connected]
    all_rtts = [r for c in ok for r in c.rtts]
    connect_latencies = [c.connect_latency for c in ok if c.connect_latency]
    total_sent = sum(c.sent for c in sim_clients)
    total_confirmed = sum(c.confirmed for c in sim_clients)

    avg_connect_ms = round(statistics.mean(connect_latencies) * 1000, 2) if connect_latencies else 0
    avg_rtt_ms = round(statistics.mean(all_rtts) * 1000, 2) if all_rtts else 0
    p95_rtt_ms = round(sorted(all_rtts)[int(len(all_rtts) * 0.95) - 1] * 1000, 2) if len(all_rtts) >= 5 else avg_rtt_ms
    throughput = round(total_confirmed / duration, 2) if duration > 0 else 0

    cpu_vals = [s[0] for s in samples]
    mem_vals = [s[1] for s in samples]
    cpu_avg = round(statistics.mean(cpu_vals), 2) if cpu_vals else 0
    cpu_peak = round(max(cpu_vals), 2) if cpu_vals else 0
    mem_avg = round(statistics.mean(mem_vals), 2) if mem_vals else 0
    mem_peak = round(max(mem_vals), 2) if mem_vals else 0

    row = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'label': args.label,
        'num_clients': args.clients,
        'messages_per_client': args.messages,
        'total_messages_sent': total_sent,
        'total_messages_confirmed': total_confirmed,
        'connect_success': len(ok),
        'connect_failed': len(failed),
        'avg_connect_latency_ms': avg_connect_ms,
        'avg_rtt_latency_ms': avg_rtt_ms,
        'p95_rtt_latency_ms': p95_rtt_ms,
        'throughput_msgs_per_sec': throughput,
        'duration_sec': round(duration, 2),
        'cpu_percent_avg': cpu_avg,
        'cpu_percent_peak': cpu_peak,
        'mem_mb_avg': mem_avg,
        'mem_mb_peak': mem_peak,
    }

    write_header = not os.path.exists(args.output)
    with open(args.output, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)

    print('\n--- Results ---')
    for k, v in row.items():
        print(f'{k:28s}: {v}')
    if failed:
        print(f'\n[!] {len(failed)} client(s) failed to connect/authenticate:')
        for c in failed[:5]:
            print(f'    {c.username}: {c.error}')
    print(f'\nAppended to {args.output}')


if __name__ == '__main__':
    main()
