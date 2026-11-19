#!/usr/bin/env python3
"""Serve local computer statistics as JSON over the LAN."""

import argparse
import json
import os
import shutil
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"timestamp": 0}
        self.previous_io = {}
        self.previous_time = 0.0

    def run(self):
        psutil.cpu_percent(None)
        while True:
            started = time.monotonic()
            try:
                sample = self.sample()
                with self.lock:
                    self.data = sample
            except Exception as exc:
                print(f"Sample error: {exc}", flush=True)
            time.sleep(max(0.0, 1.0 - (time.monotonic() - started)))

    def sample(self):
        now = time.monotonic()
        elapsed = max(0.001, now - self.previous_time) if self.previous_time else 1.0
        apps = defaultdict(lambda: {
            "name": "", "cpu": 0.0, "memory_bytes": 0,
            "read_bps": 0.0, "write_bps": 0.0,
        })
        current_io = {}

        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "unknown").strip() or "unknown"
                row = apps[name]
                row["name"] = name
                row["cpu"] += proc.cpu_percent(None)
                row["memory_bytes"] += proc.memory_info().rss
                try:
                    counters = proc.io_counters()
                    io = (counters.read_bytes, counters.write_bytes)
                    current_io[proc.pid] = io
                    previous = self.previous_io.get(proc.pid)
                    if previous:
                        row["read_bps"] += max(0, io[0] - previous[0]) / elapsed
                        row["write_bps"] += max(0, io[1] - previous[1]) / elapsed
                except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
                    pass
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue

        self.previous_io = current_io
        self.previous_time = now
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/" if os.name != "nt" else os.environ.get("SystemDrive", "C:") + "\\")
        net = psutil.net_io_counters()
        try:
            load = list(os.getloadavg())
        except (AttributeError, OSError):
            load = None

        def top(metric):
            return [dict(app) for app in sorted(apps.values(), key=lambda app: app[metric], reverse=True)[:8]]

        return {
            "timestamp": time.time(),
            "hostname": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "computer"),
            "uptime": time.time() - psutil.boot_time(),
            "cpu_percent": psutil.cpu_percent(None),
            "cpu_count": psutil.cpu_count() or 0,
            "load": load,
            "memory": {"total": memory.total, "used": memory.used, "available": memory.available, "percent": memory.percent},
            "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk.percent},
            "network": {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv},
            "cpu_apps": top("cpu"),
            "memory_apps": top("memory_bytes"),
            "io_apps": sorted(
                (dict(app) for app in apps.values()),
                key=lambda app: app["read_bps"] + app["write_bps"],
                reverse=True,
            )[:8],
        }

    def snapshot(self):
        with self.lock:
            return self.data


def handler_for(collector, token):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/stats"):
                self.send_error(404)
                return
            if token and self.headers.get("X-Stats-Token") != token:
                self.send_error(401, "Invalid or missing stats token")
                return
            body = json.dumps(collector.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            # The display polls every second; normal request logging is too noisy.
            if not (len(args) > 1 and str(args[1]).startswith("2")):
                print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Serve computer stats over a LAN")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8282)
    parser.add_argument("--token", default=os.environ.get("STATS_TOKEN", ""))
    args = parser.parse_args()

    collector = Collector()
    threading.Thread(target=collector.run, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(collector, args.token))
    print(f"Serving stats at http://{args.host}:{args.port}/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
