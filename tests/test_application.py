import unittest

import bot


class ApplicationBuilderTests(unittest.TestCase):
    def test_build_application_registers_pdf_cleanup(self) -> None:
        application = bot._build_application("123456:TEST_TOKEN")

        self.assertIs(application.post_shutdown, bot._cleanup_pdf_temp_dir)


if __name__ == "__main__":
    unittest.main()
