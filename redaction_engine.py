"""
redaction_engine.py

Architecture
------------
Docling (docling.document_converter.DocumentConverter) is the universal
parsing layer. It converts supported source files to Markdown, then the
selected built-in PII patterns, custom keywords, and custom regex rules are
applied to that Markdown. Every successful redaction is written as a .md file.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

from patterns import DOCLING_PREVIEW_EXTENSIONS

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)

_converter = None
_converter_lock = Lock()


@dataclass
class Rule:
    label: str
    pattern: re.Pattern


LogFn = Optional[Callable[[str], None]]


class _DependencyNoiseFilter(logging.Filter):
    _message_prefixes = (
        "Using a slow image processor as `use_fast` is unset",
        "The text detection result is empty",
        "RapidOCR returned empty result!",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(
            self._message_prefixes
        )


def _configure_dependency_logging() -> None:
    logger_names = (
        "transformers.models.auto.image_processing_auto",
        "RapidOCR",
        "docling.models.stages.ocr.rapid_ocr_model",
    )
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(log_filter, _DependencyNoiseFilter)
            for log_filter in logger.filters
        ):
            logger.addFilter(_DependencyNoiseFilter())


def _build_converter() -> DocumentConverter:
    import torch

    _configure_dependency_logging()
    pipeline_options = PdfPipelineOptions()
    device = str(pipeline_options.accelerator_options.device)

    if torch.backends.mps.is_available() and device in {"auto", "mps"}:
        pipeline_options.layout_options.engine_options.compile_model = False

    if os.environ.get("DYNO"):
        pipeline_options.accelerator_options.num_threads = 1
        pipeline_options.ocr_options = RapidOcrOptions(
            backend="onnxruntime",
            use_cls=False,
        )
        pipeline_options.do_table_structure = False
        pipeline_options.ocr_batch_size = 1
        pipeline_options.layout_batch_size = 1
        pipeline_options.table_batch_size = 1
        pipeline_options.queue_max_size = 1
        pipeline_options.layout_options.engine_options.compile_model = False

    format_options = {
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
    }
    return DocumentConverter(format_options=format_options)


class RedactionEngine:
    def __init__(
        self,
        rules: list[Rule],
        style: str = "box",          # "box" (block glyphs) or "label" ([REDACTED])
        log_fn: LogFn = None,
    ):
        self.rules = rules
        self.style = style
        self._log = log_fn or (lambda msg: None)

    # ------------------------------------------------------------------ #
    # docling (detection / preview)
    # ------------------------------------------------------------------ #
    @property
    def converter(self):
        global _converter

        if _converter is None:
            with _converter_lock:
                if _converter is None:
                    self._log("Initializing shared docling document converter...")
                    _converter = _build_converter()
        return _converter

    def extract_text_with_docling(self, path: Path) -> str:
        """Universal text extraction used for detection/preview across formats."""
        converter = self.converter
        with _converter_lock:
            result = converter.convert(str(path))
        return result.document.export_to_markdown()

    def find_matches(self, text: str) -> dict[str, int]:
        """Return {label: match_count} for the current rule set against text."""
        counts: dict[str, int] = {}
        for rule in self.rules:
            found = rule.pattern.findall(text)
            if found:
                counts[rule.label] = counts.get(rule.label, 0) + len(found)
        return counts

    def scan(self, path: Path) -> dict[str, int]:
        """Run docling extraction + detection, used for the pre-redaction preview."""
        ext = path.suffix.lower()
        if ext not in DOCLING_PREVIEW_EXTENSIONS:
            return {}
        try:
            text = self.extract_text_with_docling(path)
        except Exception as exc:  # noqa: BLE001
            self._log(f"  (docling preview failed for {path.name}: {exc})")
            return {}
        return self.find_matches(text)

    # ------------------------------------------------------------------ #
    # Markdown redaction export
    # ------------------------------------------------------------------ #
    def redact(self, path: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            return self._redact_markdown(path, output_dir)
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ERROR redacting {path.name}: {exc}")
            self._log(traceback.format_exc(limit=2))
            raise

    # ------------------------------------------------------------------ #
    # string-level substitution shared by text-bearing formats
    # ------------------------------------------------------------------ #
    def _redact_string(self, text: str) -> tuple[str, int]:
        count = 0

        def make_sub():
            def _sub(m: re.Match) -> str:
                nonlocal count
                count += 1
                if self.style == "label":
                    return "[REDACTED]"
                return "\u2588" * max(len(m.group(0)), 1)
            return _sub

        for rule in self.rules:
            text = rule.pattern.sub(make_sub(), text)
        return text, count

    def _redact_markdown(self, path: Path, output_dir: Path) -> Path:
        if path.suffix.lower() in (".txt", ".md"):
            markdown = path.read_text(encoding="utf-8", errors="replace")
        else:
            markdown = self.extract_text_with_docling(path)

        redacted_markdown, count = self._redact_string(markdown)
        out_path = self._unique_path(output_dir, path.stem, "_redacted.md")
        out_path.write_text(redacted_markdown, encoding="utf-8")
        self._log(f"  MARKDOWN: {count} match(es) redacted.")
        return out_path

    # ------------------------------------------------------------------ #
    @staticmethod
    def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
        candidate = directory / f"{stem}{suffix}"
        i = 2
        while candidate.exists():
            candidate = directory / f"{stem}{suffix.rsplit('.', 1)[0]}_{i}.{suffix.rsplit('.', 1)[1]}"
            i += 1
        return candidate

def process_document_job(input_reference):
    # Retrieve/open the PDF.
    # Call your existing redaction code here.
    # Save/upload the finished file.

    return {
        "status": "complete",
        "output_reference": "...",
    }

def build_rules(
    builtin_selected: dict[str, str],
    custom_keywords: list[str],
    custom_regex: list[str],
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> list[Rule]:
    """Compile the user's UI selections into a list of Rule objects."""
    flags = 0 if case_sensitive else re.IGNORECASE
    rules: list[Rule] = []

    for label, pattern in builtin_selected.items():
        rules.append(Rule(label, re.compile(pattern, flags)))

    for kw in custom_keywords:
        kw = kw.strip()
        if not kw:
            continue
        escaped = re.escape(kw)
        if whole_word:
            escaped = rf"\b{escaped}\b"
        rules.append(Rule(f'Keyword: "{kw}"', re.compile(escaped, flags)))

    for rx in custom_regex:
        rx = rx.strip()
        if not rx:
            continue
        try:
            rules.append(Rule(f"Custom regex: {rx}", re.compile(rx, flags)))
        except re.error as exc:
            raise ValueError(f"Invalid regex '{rx}': {exc}") from exc

    return rules
