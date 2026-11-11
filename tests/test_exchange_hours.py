"""Timezone and market gate regression checks for UTC-scheduled PAPER orders."""
import unittest
from datetime import datetime, timezone

from insider.portfolio import trading_window


class ExchangeHoursTests(unittest.TestCase):
    def market(self, stamp, clock=True):
        return trading_window(datetime.fromisoformat(stamp).astimezone(timezone.utc),
                              {"is_open": clock})

    def test_november_standard_time_not_before_945_et(self):
        # 14:44 UTC = 09:44 EST.
        self.assertFalse(self.market("2026-11-10T14:44:00+00:00"))
        self.assertTrue(self.market("2026-11-10T14:45:00+00:00"))

    def test_november_standard_time_not_after_1540_et(self):
        self.assertTrue(self.market("2026-11-10T20:40:00+00:00"))
        self.assertFalse(self.market("2026-11-10T20:41:00+00:00"))

    def test_july_daylight_time_not_before_945_et(self):
        # 13:45 UTC = 09:45 EDT.
        self.assertFalse(self.market("2026-07-14T13:44:00+00:00"))
        self.assertTrue(self.market("2026-07-14T13:45:00+00:00"))

    def test_july_daylight_time_not_after_1540_et(self):
        self.assertTrue(self.market("2026-07-14T19:40:00+00:00"))
        self.assertFalse(self.market("2026-07-14T19:41:00+00:00"))

    def test_exchange_closed_even_during_eligible_clock_window(self):
        self.assertFalse(self.market("2026-11-10T16:00:00+00:00", clock=False))

    def test_saturday_rejected_even_if_clock_stale(self):
        self.assertFalse(self.market("2026-11-14T16:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
