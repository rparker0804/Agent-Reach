"""Where flagged opportunities go. Add a class with ``report``/``status`` (e.g. a
Telegram or webhook sink) and list it in ``build_reporters``."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.table import Table

from .models import Signal

_STYLE = {"CALL": "bold green", "PUT": "bold red", "INFO": "bold cyan"}


class Reporter(Protocol):
    def report(self, signal: Signal) -> None: ...
    def status(self, rows: list[dict]) -> None: ...


class ConsoleReporter:
    def __init__(self, console: Console | None = None, show_status: bool = True) -> None:
        self.console = console or Console()
        self.show_status = show_status

    def report(self, s: Signal) -> None:
        arrow = {"CALL": "▲ CALL", "PUT": "▼ PUT"}.get(s.direction, s.direction)
        self.console.print(
            f"[dim]{s.time_utc:%Y-%m-%d %H:%M:%S} UTC[/] "
            f"[bold]{s.asset:<12}[/] [{_STYLE.get(s.direction, 'bold')}]{arrow:<6}[/] "
            f"[yellow]{s.rule}[/]: {s.condition}  [dim]@ {s.price:g}[/]"
        )

    def status(self, rows: list[dict]) -> None:
        if not self.show_status or not rows:
            return
        t = Table(title="Feed status", show_lines=False, header_style="bold")
        for col in ("asset", "ticks/min", "bars", "last price", "warm"):
            t.add_column(col)
        for r in rows:
            t.add_row(
                r["asset"],
                f"{r['ticks_per_min']:.0f}",
                str(r["bars"]),
                f"{r['last_price']:g}" if r["last_price"] is not None else "-",
                "yes" if r["warm"] else f"{r['bars']}/{r['min_bars']}",
            )
        self.console.print(t)


class JsonlReporter:
    """One JSON object per signal — easy to tail, grep or load into pandas."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def report(self, s: Signal) -> None:
        rec = asdict(s) | {"time_utc": s.time_utc.isoformat()}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def status(self, rows: list[dict]) -> None:
        pass


def build_reporters(cfg: dict) -> list[Reporter]:
    reporters: list[Reporter] = []
    if cfg.get("console", True):
        reporters.append(ConsoleReporter(show_status=cfg.get("show_status", True)))
    if cfg.get("jsonl"):
        reporters.append(JsonlReporter(cfg["jsonl"]))
    return reporters
