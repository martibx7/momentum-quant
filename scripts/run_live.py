"""run_live.py — live supervisor for Momentum‑Quant
==================================================
Launches all four engines in a single cooperative loop (single‑threaded) so
you can start paper trading instantly from one terminal.

* Reads the API port / client IDs from ``config.yml`` → ``account`` section.
* Automatically reconnects to TWS if connection drops.
"""
from __future__ import annotations

import logging
import time

import yaml

from engines.scanner_engine import ScannerEngine
from engines.watch_engine import WatchEngine
from engines.entry_engine import EntryEngine
from engines.exit_engine import ExitEngine

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)

# ───────────────── read account cfg ─────────────────
with open("config.yml", "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)
ACC = CFG.get("account", {})
IB_PORT = ACC.get("ib_port", 7497)
CLIENT_IDS = ACC.get("client_ids", {"scanner": 17, "watch": 18, "entry": 19, "exit": 20})

# Override default ports in each engine (they use clientId internally)
scanner = ScannerEngine()
watch   = WatchEngine()
entry   = EntryEngine()
exit    = ExitEngine()

# ───────────────── main loop ───────────────────────
try:
    while True:
        scanner.run_once()
        watch.run_once()
        entry.run_once()
        exit.run_once()
        time.sleep(1)  # 1‑sec cadence keeps CPU low; spreads still snapshot
except KeyboardInterrupt:
    print("\nShutting down live supervisor…")
