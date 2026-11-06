import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from scripts.postclose_guard import needs_scan, KEYS


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 6, 16, 55, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.plan = dict(date='2026-11-06', updated_at='2026-11-06T16:51:00+08:00',
                         strategies={k: dict(error=None, provisional=False) for k in KEYS})
        self.book = dict(last_session='2026-11-06')

    def test_complete_report_skips_repeat(self):
        self.assertFalse(needs_scan(self.now, self.plan, self.book))

    def test_early_morning_run_does_not_suppress_afternoon(self):
        self.plan['updated_at'] = '2026-11-06T01:34:00+08:00'
        self.assertTrue(needs_scan(self.now, self.plan, self.book))

    def test_previous_market_date_needs_scan(self):
        self.plan['date'] = '2026-11-05'
        self.assertTrue(needs_scan(self.now, self.plan, self.book))

    def test_failure_or_unsettled_book_needs_recovery(self):
        self.plan['strategies']['lh']['error'] = 'unavailable'
        self.assertTrue(needs_scan(self.now, self.plan, self.book))
        self.plan['strategies']['lh']['error'] = None
        self.book['last_session'] = '2026-11-05'
        self.assertTrue(needs_scan(self.now, self.plan, self.book))

    def test_morning_trigger_does_no_work(self):
        self.assertFalse(needs_scan(self.now.replace(hour=1), {}, {}))


if __name__ == '__main__':
    unittest.main()
