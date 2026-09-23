import importlib.util
import sys
from pathlib import Path

# Make `otc_scanner` importable when pytest runs from the repo root or from here.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The repo-wide CI (`pytest -q` at the root) installs only Agent Reach's own deps.
# Skip this example's tests there; its own workflow installs requirements.txt.
_REQUIRED = ("pandas", "websockets")
if any(importlib.util.find_spec(m) is None for m in _REQUIRED):
    collect_ignore_glob = ["tests/*"]
