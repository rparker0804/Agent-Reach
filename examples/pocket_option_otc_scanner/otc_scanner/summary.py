"""Render a signals JSON-lines file as Markdown (used for the GitHub Actions job summary).

python -m otc_scanner.summary signals.jsonl >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def render(path: str | Path, limit: int = 200) -> str:
    p = Path(path)
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        return "### OTC scanner\n\nNo signals were flagged during this run.\n"

    by_pair = Counter(r["asset"] for r in rows)
    out = [
        "### OTC scanner",
        "",
        f"**{len(rows)} signal(s)** across {len(by_pair)} pair(s): "
        + ", ".join(f"{a} ({n})" for a, n in by_pair.most_common()),
        "",
        "| Time (UTC) | Pair | Direction | Rule | Condition | Price |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows[-limit:]:
        cond = r["condition"].replace("|", "\\|")
        out.append(
            f"| {r['time_utc'][:19].replace('T', ' ')} | {r['asset']} | {r['direction']} "
            f"| {r['rule']} | {cond} | {r['price']:g} |"
        )
    if len(rows) > limit:
        out.append(f"\n_Showing the last {limit}; the full list is in the `signals` artifact._")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(sys.argv[1] if len(sys.argv) > 1 else "signals.jsonl"))
