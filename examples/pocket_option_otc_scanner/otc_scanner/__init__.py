"""Real-time technical-signal scanner for Pocket Option OTC pairs.

Layers (each replaceable on its own):
    feeds/       data access (direct WebSocket, Playwright browser tap, simulator)
    candles.py   tick -> OHLC aggregation
    indicators/  indicator registry + built-ins
    signals/     rule registry + engine
    output.py    reporters (console, JSON lines)
    scanner.py   orchestration
"""

__version__ = "0.1.0"
