"""v0.6 CLI and enabled Actions path smoke tests, all offline."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from insider.__main__ import main


ACCOUNT = "5a5d5e5f-19fc-4aa3-9873-7b8cce1b298f"


def fake_result():
    return {
        "status": "READ_ONLY_REBALANCE_PLAN",
        "plan": {
            "account_id": ACCOUNT, "cash_usd": "975",
            "equity_usd": "1000",
            "target_weights": [{"symbol": "UPBD", "target_usd": "970"}],
            "protected_positions": ["GME"],
            "position_count": 1, "held_positions": [],
            "proposed_order": None,
        },
    }


class V06CliIntegrationTests(unittest.TestCase):
    def test_cli_dry_run_reads_purchases_and_sales_without_broker_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / "latest.json")
            history = str(Path(tmp) / "history.jsonl")
            with (patch.dict(os.environ, {
                    "MI_PAPER_ACCOUNT_ID": ACCOUNT,
                    "MI_PAPER_AUTO_ENABLED": "I-ENABLE-PAPER-AUTO"}, clear=True),
                  patch.object(sys, "argv", ["python -m insider", "rebalance-cycle",
                                             "--output", output,
                                             "--history-output", history]),
                  patch("insider.__main__.fetch_recent_form4", side_effect=[
                      [{"transaction_code": "P"}], [{"transaction_code": "S"}]
                  ]) as fetch,
                  patch("insider.__main__.rank_current_purchases",
                        return_value=[{"symbol": "UPBD"}]),
                  patch("insider.__main__.score_candidates",
                        return_value=[{"symbol": "UPBD", "score": 1.}]),
                  patch("insider.__main__.PaperClient", return_value=object()),
                  patch("insider.__main__.run_rebalance_cycle",
                        return_value=fake_result()) as run):
                self.assertEqual(main(), 0)
            doc = json.loads(Path(output).read_text())
            self.assertEqual(doc["version"], "0.6.0")
            self.assertEqual(doc["purchase_form4_rows"], 1)
            self.assertEqual(doc["sale_form4_rows"], 1)
            self.assertEqual(len(Path(history).read_text().splitlines()), 1)
            self.assertEqual(fetch.call_args_list[0].kwargs["transaction_code"], "P")
            self.assertEqual(fetch.call_args_list[1].kwargs["transaction_code"], "S")
            self.assertEqual(run.call_args.kwargs["execute"], False)

    def test_execution_requires_explicit_paper_secret_and_account(self):
        with patch.dict(os.environ, {"MI_PAPER_ACCOUNT_ID": ACCOUNT}, clear=True), patch.object(
                sys, "argv", ["python -m insider", "rebalance-cycle", "--execute"]):
            with self.assertRaises(SystemExit):
                main()
        with patch.dict(os.environ, {"MI_PAPER_AUTO_ENABLED": "I-ENABLE-PAPER-AUTO"},
                        clear=True), patch.object(
                sys, "argv", ["python -m insider", "rebalance-cycle"]):
            with self.assertRaises(SystemExit):
                main()

    def test_scheduled_workflow_executes_v06_not_fixed_dollar_v05(self):
        path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "paper-portfolio.yml"
        content = path.read_text()
        self.assertIn("rebalance-cycle --execute", content)
        self.assertNotIn("portfolio-cycle --execute", content)
        self.assertIn("MI_PAPER_AUTOMATION_ENABLED", content)
        self.assertIn("MI_PAPER_ACCOUNT_ID", content)
        self.assertIn("secrets.MI_ALPACA_PAPER_SECRET_KEY", content)
        self.assertIn("concurrency:", content)


if __name__ == "__main__":
    unittest.main()
