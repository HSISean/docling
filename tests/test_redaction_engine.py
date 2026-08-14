import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patterns import BUILTIN_PATTERNS
from redaction_engine import (
    RedactionEngine,
    _DependencyNoiseFilter,
    _build_converter,
    build_rules,
)


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

    def test_mps_layout_models_do_not_use_torch_compile(self):
        from docling.datamodel.base_models import InputFormat

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("torch.backends.mps.is_available", return_value=True),
        ):
            converter = _build_converter()

        for input_format in (InputFormat.PDF, InputFormat.IMAGE):
            engine_options = converter.format_to_options[
                input_format
            ].pipeline_options.layout_options.engine_options
            self.assertFalse(engine_options.compile_model)

    def test_heroku_uses_low_memory_docling_settings(self):
        from docling.datamodel.base_models import InputFormat

        with (
            patch.dict(os.environ, {"DYNO": "web.1"}, clear=True),
            patch("torch.backends.mps.is_available", return_value=False),
        ):
            converter = _build_converter()

        for input_format in (InputFormat.PDF, InputFormat.IMAGE):
            options = converter.format_to_options[input_format].pipeline_options
            self.assertEqual(options.accelerator_options.num_threads, 1)
            self.assertEqual(options.ocr_options.backend, "onnxruntime")
            self.assertFalse(options.ocr_options.use_cls)
            self.assertEqual(
                options.layout_options.engine_options.engine_type,
                "onnxruntime",
            )
            self.assertFalse(options.do_table_structure)
            self.assertEqual(options.ocr_batch_size, 1)
            self.assertEqual(options.layout_batch_size, 1)
            self.assertEqual(options.table_batch_size, 1)
            self.assertEqual(options.queue_max_size, 1)

    def test_docling_converter_is_shared_between_engines(self):
        first = RedactionEngine([])
        second = RedactionEngine([])
        shared_converter = object()

        with patch("redaction_engine._converter", shared_converter):
            self.assertIs(first.converter, shared_converter)
            self.assertIs(second.converter, shared_converter)

    def test_expected_dependency_noise_is_suppressed(self):
        log_filter = _DependencyNoiseFilter()
        noisy_messages = (
            "Using a slow image processor as `use_fast` is unset and a slow processor was saved",
            "The text detection result is empty",
            "RapidOCR returned empty result!",
        )
        unrelated = logging.LogRecord(
            "RapidOCR",
            logging.WARNING,
            "",
            0,
            "OCR model initialization failed",
            (),
            None,
        )

        for message in noisy_messages:
            record = logging.LogRecord(
                "dependency",
                logging.WARNING,
                "",
                0,
                message,
                (),
                None,
            )
            self.assertFalse(log_filter.filter(record))
        self.assertTrue(log_filter.filter(unrelated))


if __name__ == "__main__":
    unittest.main()
