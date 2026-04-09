from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate_journal_watch_env.py"


class GenerateJournalWatchEnvTests(unittest.TestCase):
    def run_script(self, template_text: str, existing_output: str | None = None) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            template_path = tmpdir_path / ".env.example"
            output_path = tmpdir_path / ".env"
            template_path.write_text(template_text, encoding="utf-8")
            if existing_output is not None:
                output_path.write_text(existing_output, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--template",
                    str(template_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return output_path.read_text(encoding="utf-8")

    def test_creates_from_template_and_preserves_comments(self) -> None:
        rendered = self.run_script(
            "# heading\nDJANGO_SECRET_KEY=\nSENTRY_DSN=\nPLANKA_EXTERNAL_URL=https://example.com\n"
        )
        self.assertIn("# heading\n", rendered)
        self.assertRegex(rendered, r"DJANGO_SECRET_KEY=\S+")
        self.assertIn("SENTRY_DSN=\n", rendered)
        self.assertIn("PLANKA_EXTERNAL_URL=https://example.com\n", rendered)

    def test_preserves_existing_non_empty_secret_values(self) -> None:
        rendered = self.run_script(
            "DJANGO_SECRET_KEY=\nPOSTGRES_PASSWORD=\n",
            existing_output="DJANGO_SECRET_KEY=keepme\nPOSTGRES_PASSWORD=\n",
        )
        self.assertIn("DJANGO_SECRET_KEY=keepme\n", rendered)
        self.assertRegex(rendered, r"POSTGRES_PASSWORD=\S+")

    def test_leaves_unknown_and_quoted_lines_untouched(self) -> None:
        rendered = self.run_script(
            'CUSTOM_SECRET=""\nDJANGO_DEFAULT_FROM_EMAIL="Journal Watch <noreply@example.com>"\n'
        )
        self.assertIn('CUSTOM_SECRET=""\n', rendered)
        self.assertIn(
            'DJANGO_DEFAULT_FROM_EMAIL="Journal Watch <noreply@example.com>"\n',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
