"""Verify isolated logging configuration and reload behavior."""

import importlib
import io
import logging
import unittest
from unittest.mock import patch

from logger import logs


class LoggingTests(unittest.TestCase):
    def test_reload_preserves_root_logger_configuration(self):
        root_logger = logging.getLogger()
        original_handlers = list(root_logger.handlers)
        original_level = root_logger.level

        with patch("logging.basicConfig") as basic_config:
            importlib.reload(logs)

        basic_config.assert_not_called()
        self.assertEqual(root_logger.handlers, original_handlers)
        self.assertEqual(root_logger.level, original_level)
        self.assertFalse(logs.log.propagate)

    def test_reload_reuses_application_handler(self):
        original_logger = logs.log
        original_handlers = list(logs.log.handlers)

        importlib.reload(logs)
        importlib.reload(logs)

        self.assertIs(logs.log, original_logger)
        self.assertEqual(logs.log.handlers, original_handlers)
        self.assertEqual(len(logs.log.handlers), 1)

    def test_info_message_is_emitted_once(self):
        output = io.StringIO()
        handler = logs.log.handlers[0]
        with patch.object(handler, "stream", output):
            logs.log.info("Stream initialized")

        self.assertEqual(output.getvalue().count("Stream initialized"), 1)
        self.assertIn("INFO", output.getvalue())


if __name__ == "__main__":
    unittest.main()
