"""
redaction_engine.py

Architecture
------------
Docling (docling.document_converter.DocumentConverter) is the universal
parsing layer outside Heroku. Memory-constrained Heroku dynos use lightweight
text-layer and Office XML extraction instead. The selected built-in PII
patterns, custom keywords, and custom regex rules are applied to the extracted
text, and every successful redaction is written as a .md file.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Callable, Optional
from xml.etree import ElementTree

from patterns import DOCLING_PREVIEW_EXTENSIONS

_converter = None
_converter_lock = Lock()


@dataclass
class Rule:
    label: str
    pattern: re.Pattern


LogFn = Optional[Callable[[str], None]]


class _TextOnlyHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


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


def _build_converter():
    import torch

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    _configure_dependency_logging()
    pipeline_options = PdfPipelineOptions()
    device = str(pipeline_options.accelerator_options.device)

    if torch.backends.mps.is_available() and device in {"auto", "mps"}:
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
    # extraction (detection / preview)
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

    def extract_text(self, path: Path) -> str:
        if os.environ.get("DYNO"):
            return self._extract_text_low_memory(path)
        return self.extract_text_with_docling(path)

    @staticmethod
    def _extract_text_low_memory(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".txt", ".md", ".asciidoc"}:
            return path.read_text(encoding="utf-8", errors="replace")
        if ext in {".html", ".htm"}:
            parser = _TextOnlyHtmlParser()
            parser.feed(path.read_text(encoding="utf-8", errors="replace"))
            return "\n".join(parser.parts)
        if ext == ".pdf":
            import pymupdf

            with pymupdf.open(path) as document:
                text = "\n\n".join(page.get_text() for page in document).strip()
            if not text:
                raise ValueError(
                    "This PDF has no extractable text layer. OCR requires a "
                    "higher-memory worker than this Heroku dyno."
                )
            return text
        if ext in {".docx", ".pptx", ".xlsx"}:
            with zipfile.ZipFile(path) as archive:
                parts: list[str] = []
                for name in archive.namelist():
                    if not name.endswith(".xml"):
                        continue
                    if ext == ".docx" and not name.startswith("word/"):
                        continue
                    if ext == ".pptx" and not name.startswith("ppt/slides/"):
                        continue
                    if ext == ".xlsx" and not (
                        name.startswith("xl/sharedStrings")
                        or name.startswith("xl/worksheets/")
                    ):
                        continue
                    root = ElementTree.fromstring(archive.read(name))
                    parts.extend(
                        node.text
                        for node in root.iter()
                        if node.text and node.text.strip()
                    )
            return "\n".join(parts)
        if ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:
            raise ValueError(
                "Image OCR requires a higher-memory worker than this Heroku dyno."
            )
        raise ValueError(f"Unsupported file format: {ext or '(none)'}")

    def find_matches(self, text: str) -> dict[str, int]:
        """Return {label: match_count} for the current rule set against text."""
        counts: dict[str, int] = {}
        for rule in self.rules:
            found = rule.pattern.findall(text)
            if found:
                counts[rule.label] = counts.get(rule.label, 0) + len(found)
        return counts

    def scan(self, path: Path) -> dict[str, int]:
        """Run extraction and detection for the pre-redaction preview."""
        ext = path.suffix.lower()
        if ext not in DOCLING_PREVIEW_EXTENSIONS:
            return {}
        try:
            text = self.extract_text(path)
        except Exception as exc:  # noqa: BLE001
            self._log(f"  (preview failed for {path.name}: {exc})")
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
            markdown = self.extract_text(path)

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
