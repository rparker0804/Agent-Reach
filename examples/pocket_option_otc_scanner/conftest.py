import sys
from pathlib import Path

# Make `otc_scanner` importable when pytest runs from the repo root or from here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
