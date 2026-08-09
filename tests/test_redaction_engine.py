import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patterns import BUILTIN_PATTERNS
from redaction_engine import RedactionEngine, build_rules


class RedactionEngineTests(unittest.TestCase):
    ein_label = "Employer Identification Numbers (EINs)"

    def test_ein_builtin_redacts_valid_ein(self):
        rules = build_rules(
            {self.ein_label: BUILTIN_PATTERNS[self.ein_label]},
            [],
            [],
        )
        engine = RedactionEngine(rules, style="label")

        redacted, count = engine._redact_string("Tax ID: 12-3456789")

        self.assertEqual(redacted, "Tax ID: [REDACTED]")
        self.assertEqual(count, 1)

    def test_all_source_formats_are_exported_as_markdown(self):
        rules = build_rules(
            {self.ein_label: BUILTIN_PATTERNS[self.ein_label]},
            [],
            [],
        )
        engine = RedactionEngine(rules, style="label")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "tax-form.pdf"
            source.write_bytes(b"placeholder")
            output_dir = root / "output"

            with patch.object(
                engine,
                "extract_text_with_docling",
                return_value="# Tax form\n\nEIN: 12-3456789\n",
            ):
                output = engine.redact(source, output_dir)

            self.assertEqual(output.name, "tax-form_redacted.md")
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "# Tax form\n\nEIN: [REDACTED]\n",
            )


if __name__ == "__main__":
    unittest.main()
