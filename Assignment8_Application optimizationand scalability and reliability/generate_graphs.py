#!/usr/bin/env python3
"""
Assignment 8 — Task 5: Performance Evaluation.

Reads performance_results.csv (produced by load_test.py, one row per
run) and generates before/after comparison graphs into graphs/:

  1. latency_comparison.png     - avg RTT latency vs. concurrent clients
  2. throughput_comparison.png  - throughput vs. concurrent clients
  3. cpu_comparison.png         - server CPU% vs. concurrent clients
  4. memory_comparison.png      - server memory (MB) vs. concurrent clients

Usage:
    python3 generate_graphs.py [performance_results.csv]
"""

import sys
import os
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

INPUT = sys.argv[1] if len(sys.argv) > 1 else 'performance_results.csv'
OUTDIR = 'graphs'

COLORS = {'before': '#B85042', 'after': '#02C39A'}
MARKERS = {'before': 'o', 'after': 's'}


def plot_metric(df, ycol, ylabel, title, filename):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for label, group in df.groupby('label'):
        group = group.sort_values('num_clients')
        ax.plot(group['num_clients'], group[ycol],
                marker=MARKERS.get(label, 'o'), linewidth=2, markersize=7,
                color=COLORS.get(label, None), label=label.capitalize())
    ax.set_xlabel('Concurrent Clients')
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = os.path.join(OUTDIR, filename)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f'  wrote {path}')


def main():
    if not os.path.exists(INPUT):
        print(f'[ERROR] {INPUT} not found. Run load_test.py first to generate it.')
        sys.exit(1)

    os.makedirs(OUTDIR, exist_ok=True)
    df = pd.read_csv(INPUT)

    if df.empty:
        print(f'[ERROR] {INPUT} is empty.')
        sys.exit(1)

    print(f'Loaded {len(df)} run(s) from {INPUT}:')
    print(df[['timestamp', 'label', 'num_clients', 'avg_rtt_latency_ms',
              'throughput_msgs_per_sec', 'cpu_percent_avg', 'mem_mb_avg']].to_string(index=False))
    print(f'\nGenerating graphs into {OUTDIR}/ ...')

    plot_metric(df, 'avg_rtt_latency_ms', 'Avg Round-Trip Latency (ms)',
                'Message Latency vs. Concurrent Clients', 'latency_comparison.png')

    plot_metric(df, 'throughput_msgs_per_sec', 'Throughput (messages/sec)',
                'Throughput vs. Concurrent Clients', 'throughput_comparison.png')

    if df['cpu_percent_avg'].sum() > 0:
        plot_metric(df, 'cpu_percent_avg', 'Server CPU Usage (%)',
                    'Server CPU Usage vs. Concurrent Clients', 'cpu_comparison.png')
    else:
        print('  skipping cpu_comparison.png (no CPU samples — pass --server-pid to load_test.py)')

    if df['mem_mb_avg'].sum() > 0:
        plot_metric(df, 'mem_mb_avg', 'Server Memory Usage (MB)',
                    'Server Memory Usage vs. Concurrent Clients', 'memory_comparison.png')
    else:
        print('  skipping memory_comparison.png (no memory samples — pass --server-pid to load_test.py)')

    print('\nDone.')


if __name__ == '__main__':
    main()
