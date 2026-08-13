#!/usr/bin/env python3

from datetime import datetime
import unittest

import render_submissions as submissions


class ConsentReminderTests(unittest.TestCase):
    def test_explicit_third_reminder_stops_future_reminders(self) -> None:
        row = {
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-10 08:00:00.000 SAST",
            "consent_reminder_count": "3",
        }

        self.assertEqual(submissions.consent_reminder_count(row), 3)
        self.assertFalse(
            submissions.consent_reminder_due(
                row,
                datetime(2026, 8, 20, 8, 0, tzinfo=submissions.SAST),
            )
        )

    def test_legacy_rows_infer_reminders_from_existing_timestamps(self) -> None:
        row = {
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-07 08:00:00.000 SAST",
        }

        self.assertEqual(submissions.consent_reminder_count(row), 2)
        self.assertTrue(
            submissions.consent_reminder_due(
                row,
                datetime(2026, 8, 10, 8, 0, tzinfo=submissions.SAST),
            )
        )

    def test_legacy_rows_are_capped_at_three_inferred_reminders(self) -> None:
        row = {
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-20 08:00:00.000 SAST",
        }

        self.assertEqual(submissions.consent_reminder_count(row), 3)
        self.assertFalse(
            submissions.consent_reminder_due(
                row,
                datetime(2026, 8, 30, 8, 0, tzinfo=submissions.SAST),
            )
        )


if __name__ == "__main__":
    unittest.main()
