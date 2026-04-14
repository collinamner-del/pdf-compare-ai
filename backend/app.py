"""
Packaging PDF Comparison & Audit API - WITH HIGHLIGHTING

Compares two packaging PDFs, extracts text intelligently, segments it into logical blocks,
matches corresponding sections, and produces a structured audit report with clear highlighting.
"""

from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher, ndiff
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ============================================================================
# App setup
# ============================================================================

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_audit")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))


# ============================================================================
# Data structures
# ============================================================================

@dataclass
class Segment:
    type: str
    content: str
    lines: int = 0
    chars: int = 0
    index: int = 0
    page_start: Optional[int] = None
    page_end: Optional[int] = None


@dataclass
class MatchResult:
    seg_a: Segment
    seg_b: Segment
    score: float
    diff: Dict[str, Any]


# ============================================================================
# Text extraction
# ============================================================================

def extract_text_intelligently(file_storage) -> str:
    """
    Extract text from a PDF with lightweight structure preservation.
    """
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        pages_text: List[str] = []
        with pdfplumber.open(file_storage) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text(layout=True) or page.extract_text() or ""
                text = text.strip()
                if text:
                    pages_text.append(f"[PAGE {page_number}]\n{text}")

        return "\n\n".join(pages_text).strip()

    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc


# ============================================================================
# Segmentation
# ============================================================================

class TextSegmenter:
    """
    Splits text into logical blocks for comparison.
    """

    SECTION_PATTERNS = {
        "PRODUCT_NAME": [
            r"^[A-Z0-9][A-Z0-9\s&\-/.,()]{5,70}$",
            r"^(?:product name|brand name)\s*[:\-]?\s*.+$",
        ],
        "INGREDIENTS": [
            r"^\s*ingredients?\s*[:\-]?\s*.*$",
            r"^\s*composition\s*[:\-]?\s*.*$",
            r"^\s*constituents\s*[:\-]?\s*.*$",
        ],
        "NUTRITION": [
            r"^\s*nutrition(?:al)?(?: information)?\s*[:\-]?\s*.*$",
            r"^\s*per\s+100\b.*$",
        ],
        "ALLERGENS": [
            r"^\s*allerg(?:en|ens)\b.*$",
            r"^\s*may contain\b.*$",
            r"^\s*contains?\b.*$",
        ],
        "STORAGE": [
            r"^\s*storage\b.*$",
            r"^\s*store\b.*$",
            r"^\s*keep\b.*$",
            r"^\s*refrigerat(?:e|ion)\b.*$",
        ],
        "INSTRUCTIONS": [
            r"^\s*(?:instructions?|directions?|preparation|method)\b.*$",
        ],
        "COMPANY": [
            r"^\s*(?:made by|produced by|manufacturer|distributed by)\b.*$",
        ],
    }

    @classmethod
    def segment(cls, text: str) -> List[Segment]:
        """
        Segment text into logical blocks using section headers and blank-line boundaries.
        """
        lines = [ln.rstrip() for ln in text.splitlines()]
        segments: List[Segment] = []

        current_type = "GENERAL"
        current_lines: List[str] = []
        current_page_start: Optional[int] = None
        current_page_end: Optional[int] = None

        for line in lines:
            stripped = line.strip()

            if not stripped:
                if current_lines:
                    cls._flush_segment(
                        segments=segments,
                        seg_type=current_type,
                        seg_lines=current_lines,
                        page_start=current_page_start,
                        page_end=current_page_end,
                    )
                    current_type = "GENERAL"
                    current_lines = []
                    current_page_start = None
                    current_page_end = None
                continue

            page_match = re.match(r"^\[PAGE\s+(\d+)\]$", stripped, re.I)
            if page_match:
                page_num = int(page_match.group(1))
                if current_lines:
                    cls._flush_segment(
                        segments=segments,
                        seg_type=current_type,
                        seg_lines=current_lines,
                        page_start=current_page_start,
                        page_end=current_page_end,
                    )
                    current_lines = []
                    current_type = "GENERAL"
                    current_page_start = None
                    current_page_end = None
                current_lines.append(stripped)
                continue

            detected = cls._detect_section(stripped)
            if detected and current_lines:
                cls._flush_segment(
                    segments=segments,
                    seg_type=current_type,
                    seg_lines=current_lines,
                    page_start=current_page_start,
                    page_end=current_page_end,
                )
                current_type = detected
                current_lines = [stripped]
                continue

            if current_page_start is None:
                current_page_start = cls._current_page_from_lines(current_lines)
            current_lines.append(stripped)

        if current_lines:
            cls._flush_segment(
                segments=segments,
                seg_type=current_type,
                seg_lines=current_lines,
                page_start=current_page_start,
                page_end=current_page_end,
            )

        # Reindex after filtering tiny fragments
        cleaned: List[Segment] = []
        for idx, seg in enumerate(segments, start=1):
            seg.index = idx
            cleaned.append(seg)

        return cleaned

    @classmethod
    def _detect_section(cls, line: str) -> Optional[str]:
        for section_type, patterns in cls.SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, line, flags=re.I):
                    return section_type
        return None

    @staticmethod
    def _current_page_from_lines(lines: List[str]) -> Optional[int]:
        for line in reversed(lines):
            m = re.match(r"^\[PAGE\s+(\d+)\]$", line, re.I)
            if m:
                return int(m.group(1))
        return None

    @classmethod
    def _flush_segment(
        cls,
        segments: List[Segment],
        seg_type: str,
        seg_lines: List[str],
        page_start: Optional[int],
        page_end: Optional[int],
    ) -> None:
        content_lines = [ln for ln in seg_lines if not re.match(r"^\[PAGE\s+\d+\]$", ln, re.I)]
        content = cls._normalize_whitespace(" ".join(content_lines))
        if len(content) < 8:
            return

        pages = [int(m.group(1)) for ln in seg_lines for m in [re.match(r"^\[PAGE\s+(\d+)\]$", ln, re.I)] if m]
        seg = Segment(
            type=seg_type,
            content=content,
            lines=len(content_lines),
            chars=len(content),
            page_start=min(pages) if pages else page_start,
            page_end=max(pages) if pages else page_end,
        )
        segments.append(seg)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()


# ============================================================================
# Matching and diffing WITH HIGHLIGHTING
# ============================================================================

class SegmentMatcher:
    """
    Greedy matcher for segment pairs with highlighting.
    """

    def __init__(self, segments_a: List[Segment], segments_b: List[Segment]):
        self.segments_a = segments_a
        self.segments_b = segments_b

    def match(self) -> Tuple[List[MatchResult], List[Segment], List[Segment]]:
        matched_pairs: List[MatchResult] = []
        matched_a: set[int] = set()
        matched_b: set[int] = set()

        for idx_a, seg_a in enumerate(self.segments_a):
            best_idx_b: Optional[int] = None
            best_score = -1.0

            for idx_b, seg_b in enumerate(self.segments_b):
                if idx_b in matched_b:
                    continue

                score = self._score_match(seg_a, seg_b, idx_a, idx_b)
                if score > best_score:
                    best_score = score
                    best_idx_b = idx_b

            if best_idx_b is not None and best_score >= 55:
                seg_b = self.segments_b[best_idx_b]
                diff = self._detailed_diff(seg_a.content, seg_b.content)
                matched_pairs.append(MatchResult(seg_a=seg_a, seg_b=seg_b, score=best_score, diff=diff))
                matched_a.add(idx_a)
                matched_b.add(best_idx_b)

        deleted = [seg for i, seg in enumerate(self.segments_a) if i not in matched_a]
        added = [seg for i, seg in enumerate(self.segments_b) if i not in matched_b]

        return matched_pairs, deleted, added

    def _score_match(self, seg_a: Segment, seg_b: Segment, idx_a: int, idx_b: int) -> float:
        text_sim = SequenceMatcher(None, seg_a.content.lower(), seg_b.content.lower()).ratio() * 100
        type_bonus = 100.0 if seg_a.type == seg_b.type else 0.0

        if max(len(self.segments_a), len(self.segments_b)) > 1:
            order_bonus = 100.0 * (1.0 - abs(idx_a - idx_b) / max(len(self.segments_a), len(self.segments_b)))
        else:
            order_bonus = 100.0

        return (0.72 * text_sim) + (0.18 * type_bonus) + (0.10 * order_bonus)

    def _detailed_diff(self, text_a: str, text_b: str) -> Dict[str, Any]:
        """Create detailed diff with highlighting"""
        if text_a == text_b:
            return {
                "status": "IDENTICAL",
                "similarity": 100.0,
                "changes": [],
                "highlighted_b": text_b,
            }

        similarity = SequenceMatcher(None, text_a, text_b).ratio() * 100
        changes = self._token_diff(text_a, text_b)
        highlighted_b = self._create_highlighted_text(text_a, text_b)

        if similarity >= 98:
            status = "IDENTICAL"
        elif similarity >= 85:
            status = "MINOR_CHANGE"
        else:
            status = "SIGNIFICANT_CHANGE"

        return {
            "status": status,
            "similarity": round(similarity, 1),
            "changes": changes,
            "highlighted_b": highlighted_b,
        }

    def _token_diff(self, text_a: str, text_b: str) -> List[Dict[str, str]]:
        """Compact token diff for summary"""
        tokens_a = text_a.split()
        tokens_b = text_b.split()

        changes: List[Dict[str, str]] = []
        current: List[str] = []
        current_type: Optional[str] = None

        def flush() -> None:
            nonlocal current, current_type
            if current and current_type:
                payload = " ".join(current).strip()
                if payload:
                    changes.append({"type": current_type, "content": payload})
            current = []
            current_type = None

        for token in ndiff(tokens_a, tokens_b):
            marker = token[:2]
            value = token[2:]

            if marker == "  ":
                flush()
            elif marker == "- ":
                if current_type not in ("DELETED", None):
                    flush()
                current_type = "DELETED"
                current.append(value)
            elif marker == "+ ":
                if current_type not in ("ADDED", None):
                    flush()
                current_type = "ADDED"
                current.append(value)

        flush()
        return changes[:12]

    def _create_highlighted_text(self, text_a: str, text_b: str) -> str:
        """Create highlighted version with <mark> and <del> tags"""
        tokens_a = text_a.split()
        tokens_b = text_b.split()
        
        result: List[str] = []
        
        for token in ndiff(tokens_a, tokens_b):
            marker = token[:2]
            value = token[2:]
            
            if marker == "  ":
                result.append(value)
            elif marker == "- ":
                # Deleted word - show in red with strikethrough
                result.append(f'<span style="color:#dc2626;text-decoration:line-through;background:#fee2e2;">{value}</span>')
            elif marker == "+ ":
                # Added word - show in yellow highlight
                result.append(f'<span style="background-color:#fbbf24;font-weight:bold;">{value}</span>')
        
        return " ".join(result)


# ============================================================================
# Report building - CLEAN TABLE FORMAT
# ============================================================================

def build_report_rows(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    row_id = 1

    for match in matches:
        diff = match.diff
        impact = ""
        if diff["status"] == "IDENTICAL":
            impact = "✓ No changes"
        elif diff["status"] == "MINOR_CHANGE":
            impact = f"⚠️ Minor: {len(diff['changes'])} change(s)"
        else:
            impact = f"🔴 Significant: {len(diff['changes'])} change(s)"

        rows.append({
            "row_id": f"R{row_id}",
            "element": match.seg_a.type,
            "pdf_a_content": match.seg_a.content,
            "pdf_b_content": match.seg_b.content,
            "pdf_b_highlighted": diff.get("highlighted_b", match.seg_b.content),
            "status": diff["status"],
            "similarity": diff["similarity"],
            "impact": impact,
            "changes": diff["changes"],
            "score": round(match.score, 1),
        })
        row_id += 1

    for seg in deleted:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a_content": seg.content,
            "pdf_b_content": "[DELETED - Not in Updated Version]",
            "pdf_b_highlighted": '[DELETED - Not in Updated Version]',
            "status": "DELETED",
            "similarity": 0.0,
            "impact": "❌ Content removed",
            "changes": [],
        })
        row_id += 1

    for seg in added:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a_content": "[NEW - Not in Original Version]",
            "pdf_b_content": seg.content,
            "pdf_b_highlighted": f'<span style="background-color:#bbf7d0;font-weight:bold;border:1px solid #10b981;">{seg.content}</span>',
            "status": "ADDED",
            "similarity": 0.0,
            "impact": "✨ New content",
            "changes": [],
        })
        row_id += 1

    return rows


def build_audit_data(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> str:
    lines: List[str] = ["PACKAGING COPY AUDIT - DETAILED FINDINGS", ""]

    for i, match in enumerate(matches, start=1):
        lines.append(f"{i}. {match.seg_a.type} SECTION")
        lines.append(f"   Version A: {match.seg_a.content[:200]}")
        lines.append(f"   Version B: {match.seg_b.content[:200]}")
        lines.append(f"   Similarity: {match.diff['similarity']:.0f}%")

        if match.diff["changes"]:
            lines.append("   Changes detected:")
            for change in match.diff["changes"]:
                if change["type"] == "DELETED":
                    lines.append(f"   - REMOVED: {change['content']}")
                elif change["type"] == "ADDED":
                    lines.append(f"   - ADDED: {change['content']}")
        lines.append("")

    if deleted:
        lines.append("DELETED SECTIONS:")
        for seg in deleted:
            lines.append(f"- {seg.type}: {seg.content[:150]}")
        lines.append("")

    if added:
        lines.append("ADDED SECTIONS:")
        for seg in added:
            lines.append(f"- {seg.type}: {seg.content[:150]}")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# OpenAI summary
# ============================================================================

def generate_summary_with_openai(audit_data: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key not configured")

    prompt = f"""
You are a Senior Creative Director and Packaging QA Specialist.

Write a professional QC report based on the audit data below.

Include:
1. Executive Summary
2. Spot the Difference table
   - Copy Element
   - Version A
   - Version B
   - Impact
3. Critical Findings / Red Flags
4. Action Items checklist

Keep it concise, scannable, and suitable for print.

AUDIT DATA:
{audit_data}
""".strip()

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": "You produce precise QA reports for packaging copy comparisons."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2500,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=OPENAI_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(f"OpenAI error: {response.text}")

    data = response.json()
    return data["choices"][0]["message"]["content"]


# ============================================================================
# Validation helpers
# ============================================================================

def validate_uploaded_pdfs() -> Tuple[Any, Any]:
    if "file1" not in request.files or "file2" not in request.files:
        raise ValueError("Both PDF files are required")

    f1 = request.files["file1"]
    f2 = request.files["file2"]

    if not f1 or not f1.filename or not f2 or not f2.filename:
        raise ValueError("Files missing")

    if not f1.filename.lower().endswith(".pdf") or not f2.filename.lower().endswith(".pdf"):
        raise ValueError("Both uploads must be PDFs")

    return f1, f2


# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def home():
    return jsonify({
        "status": "API running",
        "service": "Packaging PDF Comparison & Audit API",
    })


@app.route("/compare", methods=["POST"])
def compare():
    try:
        f1, f2 = validate_uploaded_pdfs()

        text_a = extract_text_intelligently(f1)
        text_b = extract_text_intelligently(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from one or both PDFs"}), 400

        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)

        if not segments_a or not segments_b:
            return jsonify({"error": "Could not segment extracted text"}), 400

        matcher = SegmentMatcher(segments_a, segments_b)
        matched_pairs, deleted, added = matcher.match()

        report_rows = build_report_rows(matched_pairs, deleted, added)

        summary = {
            "total_rows": len(report_rows),
            "identical": sum(1 for r in report_rows if r["status"] == "IDENTICAL"),
            "modified": sum(1 for r in report_rows if r["status"] in {"MINOR_CHANGE", "SIGNIFICANT_CHANGE"}),
            "added": sum(1 for r in report_rows if r["status"] == "ADDED"),
            "deleted": sum(1 for r in report_rows if r["status"] == "DELETED"),
        }

        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Professional packaging copy audit",
                "comparison_table": report_rows,
                "summary": summary,
            }
        })

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Compare failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/summary", methods=["POST"])
def summary():
    try:
        f1, f2 = validate_uploaded_pdfs()

        text_a = extract_text_intelligently(f1)
        text_b = extract_text_intelligently(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from one or both PDFs"}), 400

        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)

        matcher = SegmentMatcher(segments_a, segments_b)
        matched_pairs, deleted, added = matcher.match()

        audit_data = build_audit_data(matched_pairs, deleted, added)
        summary_text = generate_summary_with_openai(audit_data)

        return jsonify({"summary": summary_text})

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Summary failed")
        return jsonify({"error": str(exc)}), 500


# ============================================================================
# Entrypoint
# ============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
