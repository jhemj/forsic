"""Offline clock/date semantics; no model calls or production case writes."""
from datetime import datetime, timezone
import os
import time
import unittest
from unittest.mock import patch

from forsic_plugin.runtime_clock import current_context


class RuntimeClockTests(unittest.TestCase):
    def test_actual_server_timezone_and_year_rollover(self):
        instant = datetime(2036, 1, 1, 0, 30, tzinfo=timezone.utc)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(instant.timestamp(), tz)

        previous = os.environ.get('TZ')
        try:
            with patch('forsic_plugin.runtime_clock.datetime', FrozenDatetime):
                for zone, expected in (
                    ('UTC0', '2036-01-01T00:30:00+00:00'),
                    ('EST5', '2035-12-31T19:30:00-05:00'),
                    ('JST-9', '2036-01-01T09:30:00+09:00'),
                ):
                    with self.subTest(zone=zone):
                        os.environ['TZ'] = zone
                        time.tzset()
                        clock = current_context()
                        self.assertEqual(clock['current_datetime'], expected)
                        self.assertEqual(clock['timezone'], time.tzname[0])
                        self.assertEqual(clock['basis'], 'server_clock_at_context_refresh')
        finally:
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()


if __name__ == '__main__':
    unittest.main()
