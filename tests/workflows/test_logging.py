"""日志颜色、纯文本输出和统一配置的轻量回归。"""

import logging
import unittest
from io import StringIO
from unittest.mock import patch

from lastmile_dataflow.utils.logging_config import (
    ColorFormatter, ExperimentLogFilter, Progress, setup_logging, trial_logging,
)


class LoggingTests(unittest.TestCase):
    def test_progress_is_blue_without_changing_info_or_warning_colors(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "progress", (), None)
        record.progress = True
        self.assertTrue(ColorFormatter(True).format(record).startswith("\033[34m"))
        self.assertNotIn("\033[", ColorFormatter(False).format(record))
        record.levelno = logging.WARNING
        self.assertTrue(ColorFormatter(True).format(record).startswith("\033[33m"))

    def test_fast_progress_is_silent_and_final_update_is_not_throttled(self):
        with (
            patch("lastmile_dataflow.utils.logging_config.time.monotonic", side_effect=[0, .1, 1.9, 2, 2.1]),
            self.assertLogs("test.progress", level="INFO") as logs,
        ):
            progress = Progress(logging.getLogger("test.progress"))
            progress.update("fast step")
            progress.update("another fast step")
            progress.update("trial=3/6")
            progress.update("finished trial=4/6", force=True)
        self.assertEqual([record.message for record in logs.records], ["trial=3/6", "finished trial=4/6"])
        self.assertTrue(all(record.progress for record in logs.records))

    def test_internal_logs_are_hidden_but_failures_keep_trial_context(self):
        noise = logging.LogRecord("curobo", logging.INFO, __file__, 1, "Running optimizer 1/1", (), None)
        collision = logging.LogRecord("curobo", logging.WARNING, __file__, 1,
                                      "Start or End state in collision", (), None)
        self.assertFalse(ExperimentLogFilter().filter(noise))
        self.assertTrue(ExperimentLogFilter(debug=True).filter(noise))
        self.assertTrue(ExperimentLogFilter().filter(collision))
        progress = Progress(logging.getLogger("test.progress"))
        formatter = ColorFormatter(False)
        with trial_logging("scene=one station=S0 trial=1/2", progress):
            message = formatter.format(collision)
        self.assertIn("Start or End state in collision", message)
        self.assertIn("scene=one station=S0 trial=1/2", message)
        self.assertNotIn("scene=one", formatter.format(collision))

    def test_level_colors_do_not_modify_the_record(self):
        for level, color in [(logging.INFO, 32), (logging.WARNING, 33), (logging.ERROR, 31)]:
            with self.subTest(level=level):
                record = logging.LogRecord("test", level, __file__, 1, "value=%s", ("ok",), None)
                colored = ColorFormatter(True).format(record)
                plain = ColorFormatter(False).format(record)
                self.assertEqual(colored, f"\033[{color}m{plain}\033[0m")
                self.assertNotIn("\033[", plain)
                self.assertIn("value=ok", plain)

    def test_setup_colors_only_terminals_and_does_not_duplicate_handlers(self):
        for terminal in (True, False):
            with self.subTest(terminal=terminal):
                stream = StringIO()
                root = logging.RootLogger(logging.WARNING)
                with (
                    patch("logging.root", root),
                    patch("sys.stderr", stream),
                    patch.object(stream, "isatty", return_value=terminal),
                ):
                    setup_logging()
                    setup_logging()
                    root.info("hello")
                self.assertEqual(len(root.handlers), 1)
                self.assertEqual("\033[32m" in stream.getvalue(), terminal)
                self.assertEqual(stream.getvalue().count("hello"), 1)
