"""
Built-in detection patterns for common sensitive-data categories.
Each entry: label -> raw regex string (compiled later with user-selected flags).
"""

BUILTIN_PATTERNS: dict[str, str] = {
    "Email addresses": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "Phone numbers": r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    "Social Security Numbers": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit card numbers": r"\b(?:\d[ -]?){13,19}\b",
    "IP addresses": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    "Dates (MM/DD/YYYY etc.)": r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    "US Passport numbers": r"\b[A-Z]{1,2}\d{6,9}\b",
    "URLs": r"https?://[^\s)>\]]+",
    "Bank / IBAN numbers": r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b",
    "ZIP / Postal codes": r"\b\d{5}(?:-\d{4})?\b",
}

# Order in which categories are presented in the UI
BUILTIN_PATTERN_ORDER = list(BUILTIN_PATTERNS.keys())

SUPPORTED_NATIVE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}

# Formats docling can parse for preview/detection even if native redaction
# falls back to a plain-text export.
DOCLING_PREVIEW_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".txt",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".asciidoc",
}
