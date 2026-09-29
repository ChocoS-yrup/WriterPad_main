"""Run offline tests with canonical temp paths and a bounded stall diagnostic."""

import argparse
import faulthandler
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--stall-timeout", type=float, default=120)
    args = parser.parse_args()
    if not math.isfinite(args.stall_timeout) or args.stall_timeout <= 0:
        parser.error("--stall-timeout must be finite and positive")

    # Hosted Windows can expose an 8.3 alias in TEMP. Test fixtures must use
    # canonical paths; production code still rejects aliases and reparse points.
    canonical_temp = str(Path(tempfile.gettempdir()).resolve(strict=True))
    tempfile.tempdir = canonical_temp
    os.environ["TEMP"] = canonical_temp
    os.environ["TMP"] = canonical_temp
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    print(f"Python: {sys.version.split()[0]}; canonical test temp: {canonical_temp}", flush=True)
    faulthandler.enable()

    def arm_watchdog(timeout=None):
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(
            args.stall_timeout if timeout is None else timeout, exit=True
        )

    class DiagnosticResult(unittest.TextTestResult):
        def addError(self, test, err):
            super().addError(test, err)
            self.printErrorList("ERROR", [self.errors[-1]])

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.printErrorList("FAIL", [self.failures[-1]])

        def addSubTest(self, test, subtest, err):
            errors, failures = len(self.errors), len(self.failures)
            super().addSubTest(test, subtest, err)
            self.printErrorList("ERROR", self.errors[errors:])
            self.printErrorList("FAIL", self.failures[failures:])

        def startTest(self, test):
            # The 720-save durability stress case can spend over two minutes
            # in SQLite on a busy hosted Windows runner while still making
            # progress. Keep the normal bound for every other test.
            slow_stress_case = test.id().endswith(
                "test_long_run_resources.LongRunResourceTestCase."
                "test_offline_repeated_save_keeps_immutable_durable_intents"
            )
            arm_watchdog(args.stall_timeout * 2 if slow_stress_case else None)
            super().startTest(test)

        def stopTest(self, test):
            super().stopTest(test)
            # Also bound class cleanup/setup between tests, and discovery below.
            arm_watchdog()

    arm_watchdog()
    try:
        suite = unittest.defaultTestLoader.discover(args.start_directory)
        result = unittest.TextTestRunner(verbosity=2, resultclass=DiagnosticResult).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    raise SystemExit(main())
