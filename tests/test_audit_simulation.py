"""
Integration test verifying the end-to-end autonomous agent audit simulation.
"""

import sys
from pathlib import Path

# Ensure tools directory is accessible for simulation runner
ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from simulate_agent_audit import simulate_full_audit


def test_simulate_full_audit_metrics():
    """
    Verify that the simulated multi-turn agent audit executes cleanly,
    completes in under 5 seconds, and correctly catches all 350 anomalies.
    """
    res = simulate_full_audit(quiet=True)

    assert res["duration_seconds"] < 5.0
    assert res["total_anomalies"] == 350
    assert res["content_integrity_anomalies"] == 130
    assert res["pms_discrepancies"] == 120
    assert res["missing_translations"] == 100

    # Ensure export paths were created and have non-zero size
    xlsx_file = Path(res["xlsx_path"])
    csv_file = Path(res["csv_path"])

    assert xlsx_file.exists()
    assert xlsx_file.stat().st_size > 1000

    assert csv_file.exists()
    assert csv_file.stat().st_size > 1000

    # Verify download URLs
    assert res["xlsx_download_url"].startswith("http://localhost:")
    assert res["csv_download_url"].startswith("http://localhost:")
