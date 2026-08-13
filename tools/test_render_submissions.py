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

    def test_unanswered_request_expires_three_days_after_final_reminder(self) -> None:
        row = {
            "id": "sub_expired",
            "student_number": "1234567",
            "status": "1",
            "connection_requests": "7654321",
            "consent_response": "",
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-10 08:00:00.000 SAST",
            "consent_reminder_count": "3",
        }

        before_expiry = datetime(2026, 8, 13, 7, 59, tzinfo=submissions.SAST)
        at_expiry = datetime(2026, 8, 13, 8, 0, tzinfo=submissions.SAST)

        self.assertEqual(
            submissions.expired_unanswered_consent_rows([row], before_expiry),
            [],
        )
        self.assertEqual(
            submissions.expired_unanswered_consent_rows([row], at_expiry),
            [row],
        )

    def test_cleanup_deletes_matching_request_before_expired_pool(self) -> None:
        row = {
            "id": "sub_expired",
            "student_number": "1234567",
            "direction": "to_uwc",
            "area": "Table View",
            "schedule": "mon@08:00",
            "status": "1",
            "connection_requests": "7654321",
            "consent_response": "",
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-10 08:00:00.000 SAST",
            "consent_reminder_count": "3",
        }

        class Client:
            def __init__(self) -> None:
                self.actions = []

            def list_submissions(self):
                return [row]

            def list_connection_requests(self):
                return [{
                    "id": "req_expired",
                    "student_number": "1234567",
                    "direction": "to_uwc",
                    "area": "Table View",
                    "schedule": "mon@08:00",
                }]

            def delete_connection_request(self, request_id):
                self.actions.append(("request", request_id))

            def delete_submission(self, submission_id):
                self.actions.append(("submission", submission_id))

        client = Client()
        deleted = submissions.expire_unanswered_consent(
            client,
            [row],
            apply=True,
            now=datetime(2026, 8, 13, 8, 0, tzinfo=submissions.SAST),
        )

        self.assertEqual(deleted, [row])
        self.assertEqual(
            client.actions,
            [("request", "req_expired"), ("submission", "sub_expired")],
        )

    def test_answered_or_not_finally_reminded_requests_do_not_expire(self) -> None:
        base = {
            "status": "1",
            "connection_requests": "7654321",
            "consent_email_sent_at": "2026-08-01 08:00:00.000 SAST",
            "consent_email_last_sent_at": "2026-08-10 08:00:00.000 SAST",
        }
        rows = [
            {**base, "id": "answered", "consent_response": "yes", "consent_reminder_count": "3"},
            {**base, "id": "only_two", "consent_response": "", "consent_reminder_count": "2"},
        ]

        self.assertEqual(
            submissions.expired_unanswered_consent_rows(
                rows,
                datetime(2026, 8, 20, 8, 0, tzinfo=submissions.SAST),
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
