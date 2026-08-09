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

import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from patterns import DOCLING_PREVIEW_EXTENSIONS


@dataclass
class Rule:
    label: str
    pattern: re.Pattern


LogFn = Optional[Callable[[str], None]]


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
        self._converter = None

    # ------------------------------------------------------------------ #
    # docling (detection / preview)
    # ------------------------------------------------------------------ #
    @property
    def converter(self):
        if self._converter is None:
            self._log("Initializing docling document converter...")
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
        return self._converter

    def extract_text_with_docling(self, path: Path) -> str:
        """Universal text extraction used for detection/preview across formats."""
        result = self.converter.convert(str(path))
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
