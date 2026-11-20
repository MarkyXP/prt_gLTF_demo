"""Tests for forwarding bounded frontend errors into backend logs."""

import logging
import unittest
from unittest.mock import patch

from fastapi import Request
from pydantic import ValidationError

from application.web import FrontendLogEvent, client_logs


class FrontendLogEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("application.web.LOGGER.log")
    async def test_error_report_is_logged_with_conversion_context(self, log):
        event = FrontendLogEvent.model_validate({
            "level": "error",
            "message": "GLTF parse failed",
            "stack": "Error: bad GLB",
            "source": "/static/app.js",
            "line": 42,
            "column": 7,
            "job_id": "job-123",
            "part": "assembly.prt",
        })
        request = Request({
            "type": "http",
            "headers": [(b"user-agent", b"test-browser")],
        })
        response = await client_logs(event, request)

        self.assertEqual(response.status_code, 204)
        log.assert_called_once()
        args = log.call_args.args
        self.assertEqual(args[0], logging.ERROR)
        self.assertIn("frontend level=%s job=%s", args[1])
        self.assertEqual(args[2:4], ("error", "job-123"))
        self.assertEqual(args[6:8], (42, 7))
        self.assertEqual(args[8], "GLTF parse failed")

    def test_invalid_or_oversized_reports_are_rejected(self):
        for invalid in (
            {"level": "critical", "message": "bad level"},
            {"level": "error", "message": "x" * 1201},
        ):
            with self.subTest(payload=invalid):
                with self.assertRaises(ValidationError):
                    FrontendLogEvent.model_validate(invalid)


if __name__ == "__main__":
    unittest.main()
