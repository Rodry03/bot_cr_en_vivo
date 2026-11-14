import subprocess
import sys
import unittest
from pathlib import Path

import bot


class ApplicationBuilderTests(unittest.TestCase):
    def test_build_application_registers_pdf_cleanup(self) -> None:
        application = bot._build_application("123456:TEST_TOKEN")

        self.assertIs(application.post_shutdown, bot._cleanup_pdf_temp_dir)


class HttpLoggingTests(unittest.TestCase):
    def test_http_loggers_are_warning_on_startup(self) -> None:
        """Al arrancar, httpx/httpcore no registran las URLs (con el token de Telegram).

        Se importa bot en un proceso nuevo para comprobar el estado real de arranque,
        sin depender de lo que otros tests hayan hecho con el logging."""
        script = (
            "import logging, bot\n"
            "for name in ('httpx', 'httpcore'):\n"
            "    log = logging.getLogger(name)\n"
            "    print(name, logging.getLevelName(log.level), log.isEnabledFor(logging.INFO))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(bot.__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

        self.assertEqual(
            result.stdout.split("\n")[:2],
            ["httpx WARNING False", "httpcore WARNING False"],
        )


if __name__ == "__main__":
    unittest.main()
