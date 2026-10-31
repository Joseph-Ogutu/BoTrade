"""Print H0 (the holdout start) as Unix seconds and UTC. Usage: python scripts/holdout_start.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.common.timeutil import iso_from_epoch  # noqa: E402
from derivbot.gate1.holdout import holdout_start_epoch  # noqa: E402

h0 = holdout_start_epoch(ROOT)
print(f"H0_epoch={h0}")
print(f"H0_utc={iso_from_epoch(h0)}")
