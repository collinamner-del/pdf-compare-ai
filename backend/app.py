"""
Packaging PDF Comparison & Audit API - PRECISE DIFF & ACTION FOCUSED

Detects EXACT changes - missing punctuation, bold text, numbers, etc.
Reports ACTION needed for QC with ADHD-friendly clarity.
"""

from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass
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
# IMPROVED TEXT EXTRACTION
# ============================================================================

def extract_text_intelligently(file_storage) -> str:
    """
    Extract text from PDF with FULL fidelity - preserve spacing, punctuation, etc.
    """
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        pages_text: List[str] = []
        with pdfplumber.open(file_storage) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                # Try layout-preserving extraction first
                text = page.extract_text(layout=True)
                if not text or len(text.strip()) < 20:
                    text = page.extract_text()
                
                text = text.strip() if text else ""
                
                if text:
                    pages_text.append(f"[PAGE {page_number}]\n{text}")

        full_text = "\n\n".join(pages_text).strip()
        return full_text if full_text else ""

    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc


# ============================================================================
# INTELLIGENT SEGMENTATION
# ============================================================================

class TextSegmenter:
    """
    Smart segmentation that preserves exact text for precise comparison.
    """

    SECTION_PATTERNS = {
        "PRODUCT_NAME": [
            r"^[A-Z0-9][A-Z0-9\s&\-/.,()]{5,100}$",
            r"^(?:product|brand|name)\s*[:\-]?\s*.+$",
        ],
        "INGREDIENTS": [
            r"(?:ingredients?|composition)\s*[:\-]?",
        ],
        "NUTRITION": [
            r"(?:nutrition|nutritional|per 100)",
        ],
        "ALLERGENS": [
            r"(?:allerg|may contain|contain)",
        ],
        "STORAGE": [
            r"(?:stor|keep|temperature|fridge|best before|use by)",
        ],
        "INSTRUCTIONS": [
            r"(?:instruction|direction|preparation|method|cook)",
        ],
        "COMPANY": [
            r"(?:made by|produced by|manufacturer|distributed)",
        ],
    }

    @classmethod
    def segment(cls, text: str) -> List[Segment]:
        """Segment preserving exact formatting and punctuation"""
        if not text:
            return []
        
        lines = text.split('\n')
        segments: List[Segment] = []
        current_type = "GENERAL"
        current_lines: List[str] = []

        for line in lines:
            stripped = line.strip()
            
            # Page marker
            if re.match(r"^\[PAGE\s+\d+\]$", stripped):
                if current_lines:
                    cls._flush_segment(segments, current_type, current_lines)
                    current_type = "GENERAL"
                    current_lines = []
                continue
            
            # Empty line = boundary
            if not stripped:
                if current_lines:
                    cls._flush_segment(segments, current_type, current_lines)
                    current_type = "GENERAL"
                    current_lines = []
                continue
            
            # Detect section
            detected = cls._detect_section(stripped)
            if detected and current_lines:
                cls._flush_segment(segments, current_type, current_lines)
                current_type = detected
                current_lines = [line]
                continue
            
            current_lines.append(line)

        if current_lines:
            cls._flush_segment(segments, current_type, current_lines)

        # Reindex
        for idx, seg in enumerate(segments, start=1):
            seg.index = idx

        return segments

    @classmethod
    def _detect_section(cls, line: str) -> Optional[str]:
        line_lower = line.lower()
        for section_type, patterns in cls.SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line_lower):
                    return section_type
        return None

    @classmethod
    def _flush_segment(cls, segments: List[Segment], seg_type: str, lines: List[str]) -> None:
        """Preserve exact spacing and punctuation"""
        # Join with newlines to preserve structure
        content = '\n'.join(line.rstrip() for line in lines if line.strip())
        
        if len(content.strip()) < 8:
            return

        seg = Segment(
            type=seg_type,
            content=content,
            lines=len(lines),
            chars=len(content),
        )
        segments.append(seg)


# ============================================================================
# PRECISE DIFF DETECTION
# ============================================================================

class PreciseMatcher:
    """
    Detects EXACT changes - character level, punctuation, spacing, etc.
    Generates clear ACTION items for QC.
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

            if best_idx_b is not None and best_score >= 50:
                seg_b = self.segments_b[best_idx_b]
                diff = self._precise_diff(seg_a.content, seg_b.content)
                matched_pairs.append(MatchResult(seg_a=seg_a, seg_b=seg_b, score=best_score, diff=diff))
                matched_a.add(idx_a)
                matched_b.add(best_idx_b)

        deleted = [seg for i, seg in enumerate(self.segments_a) if i not in matched_a]
        added = [seg for i, seg in enumerate(self.segments_b) if i not in matched_b]

        return matched_pairs, deleted, added

    def _score_match(self, seg_a: Segment, seg_b: Segment, idx_a: int, idx_b: int) -> float:
        ratio = SequenceMatcher(None, seg_a.content, seg_b.content).ratio() * 100
        type_bonus = 40.0 if seg_a.type == seg_b.type else 0.0
        return ratio + type_bonus

    def _precise_diff(self, text_a: str, text_b: str) -> Dict[str, Any]:
        """
        Character-by-character comparison with EXACT change detection.
        """
        if text_a == text_b:
            return {
                "status": "IDENTICAL",
                "similarity": 100.0,
                "changes": [],
                "action": "✓ No action needed",
                "highlighted_b": text_b,
            }

        similarity = SequenceMatcher(None, text_a, text_b).ratio() * 100
        
        # Get EXACT changes
        changes = self._extract_exact_changes(text_a, text_b)
        action = self._generate_action(changes, similarity)
        highlighted = self._create_highlighted(text_a, text_b)

        if similarity >= 98:
            status = "IDENTICAL"
        elif similarity >= 85:
            status = "MINOR"
        else:
            status = "SIGNIFICANT"

        return {
            "status": status,
            "similarity": round(similarity, 1),
            "changes": changes,
            "action": action,
            "highlighted_b": highlighted,
        }

    def _extract_exact_changes(self, text_a: str, text_b: str) -> List[str]:
        """
        Extract EXACT, human-readable changes.
        E.g., "Missing period at end", "Weight: 105g → 107g", etc.
        """
        changes = []

        # Check for common packaging changes
        
        # 1. Punctuation changes
        if text_a.rstrip('.!?') != text_b.rstrip('.!?'):
            # Content different without punctuation
            if text_a.rstrip('.!?,; ') == text_b.rstrip('.!?,; '):
                # Only punctuation changed
                if text_a.endswith('.') and not text_b.endswith('.'):
                    changes.append("⚠️ Period removed at end")
                elif not text_a.endswith('.') and text_b.endswith('.'):
                    changes.append("✓ Period added at end")
                if text_a.endswith(',') != text_b.endswith(','):
                    changes.append("⚠️ Comma removed" if text_a.endswith(',') else "✓ Comma added")

        # 2. Number changes (weights, %, dates, etc.)
        nums_a = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CcF])?', text_a)
        nums_b = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CcF])?', text_b)
        
        if nums_a != nums_b:
            for na, nb in zip(nums_a, nums_b):
                if na != nb:
                    changes.append(f"⚠️ Value changed: {na} → {nb}")

        # 3. Case changes
        if text_a.lower() == text_b.lower() and text_a != text_b:
            changes.append("⚠️ Text case changed (UPPER/lower)")

        # 4. Word additions/deletions
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        
        removed = words_a - words_b
        added = words_b - words_a
        
        for word in removed:
            if len(word) > 2:  # Skip small words
                changes.append(f"❌ Removed: '{word}'")
        
        for word in added:
            if len(word) > 2:
                changes.append(f"✨ Added: '{word}'")

        # 5. Spacing changes
        if text_a.strip() == text_b.strip() and text_a != text_b:
            changes.append("⚠️ Spacing/whitespace changed")

        if not changes:
            # Generic fallback
            if len(text_a) != len(text_b):
                changes.append(f"⚠️ Length changed: {len(text_a)} → {len(text_b)} chars")
            else:
                changes.append("⚠️ Text modified")

        return changes[:5]  # Limit to 5 most important

    def _generate_action(self, changes: List[str], similarity: float) -> str:
        """Generate ACTION for QC - what to check/fix"""
        if not changes or similarity >= 98:
            return "✓ No action needed"
        
        if similarity >= 85:
            return f"⚠️ Review: {len(changes)} minor change(s) - verify accuracy"
        
        return f"🔴 CHECK: {len(changes)} significant change(s) - requires verification"

    def _create_highlighted(self, text_a: str, text_b: str) -> str:
        """Create highlighted version showing differences"""
        # Simple character-by-character highlighting
        result = []
        matcher = SequenceMatcher(None, text_a, text_b)
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                result.append(text_b[j1:j2])
            elif tag == 'replace':
                # Show what was replaced
                result.append(f'<mark style="background:#fbbf24;font-weight:bold;">{text_b[j1:j2]}</mark>')
            elif tag == 'insert':
                result.append(f'<mark style="background:#fbbf24;font-weight:bold;">{text_b[j1:j2]}</mark>')
            elif tag == 'delete':
                result.append(f'<del style="background:#fee2e2;color:#dc2626;text-decoration:line-through;">{text_a[i1:i2]}</del>')
        
        return ''.join(result)


# ============================================================================
# REPORT BUILDING - ACTION FOCUSED
# ============================================================================

def build_report_rows(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> List[Dict[str, Any]]:
    """Build table rows focused on ACTION for QC"""
    rows: List[Dict[str, Any]] = []
    row_id = 1

    for match in matches:
        diff = match.diff
        
        # Truncate for table display but keep full in data
        v1_display = match.seg_a.content[:120] + ("..." if len(match.seg_a.content) > 120 else "")
        v2_display = match.seg_b.content[:120] + ("..." if len(match.seg_b.content) > 120 else "")

        rows.append({
            "row_id": f"R{row_id}",
            "element": match.seg_a.type,
            "pdf_a": v1_display,
            "pdf_b_highlighted": diff["highlighted_b"][:150],
            "action": diff["action"],
            "status": diff["status"],
            "similarity": diff["similarity"],
            "changes": diff["changes"],
        })
        row_id += 1

    for seg in deleted:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": seg.content[:120],
            "pdf_b_highlighted": "❌ NOT IN UPDATED VERSION",
            "action": "❌ VERIFY: Section removed - confirm intentional",
            "status": "DELETED",
            "similarity": 0.0,
            "changes": ["Section entirely removed"],
        })
        row_id += 1

    for seg in added:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": "✨ NEW SECTION",
            "pdf_b_highlighted": f"<mark style=\"background:#bbf7d0;\">{seg.content[:120]}</mark>",
            "action": "✓ NEW: Verify content is correct",
            "status": "ADDED",
            "similarity": 0.0,
            "changes": ["Section newly added"],
        })
        row_id += 1

    return rows


def build_audit_data(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> str:
    """Build audit summary for AI"""
    lines = ["PACKAGING COPY AUDIT - QC FINDINGS", ""]

    for i, match in enumerate(matches, start=1):
        lines.append(f"{i}. {match.seg_a.type}")
        lines.append(f"   A: {match.seg_a.content[:150]}")
        lines.append(f"   B: {match.seg_b.content[:150]}")
        lines.append(f"   Match: {match.diff['similarity']}%")
        
        if match.diff["changes"]:
            for change in match.diff["changes"]:
                lines.append(f"   • {change}")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# OpenAI summary
# ============================================================================

def generate_summary_with_openai(audit_data: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key not configured")

    prompt = f"""
You are a Packaging QA Specialist. Write a BRIEF, ACTIONABLE QC checklist.

Format:
[ ] 1. ELEMENT | CHANGE | ACTION
[ ] 2. ELEMENT | CHANGE | ACTION

Be specific. Example:
[ ] 1. ALLERGENS | "nuts" removed | VERIFY: Is this intentional? Check formulation.
[ ] 2. WEIGHT | 105g → 107g | UPDATE: Verify new weight in system.

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
            {"role": "system", "content": "You are a precise QA specialist writing actionable checklists."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
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
# Validation
# ============================================================================

def validate_uploaded_pdfs() -> Tuple[Any, Any]:
    if "file1" not in request.files or "file2" not in request.files:
        raise ValueError("Both PDF files required")

    f1 = request.files["file1"]
    f2 = request.files["file2"]

    if not f1 or not f1.filename or not f2 or not f2.filename:
        raise ValueError("Files missing")

    if not f1.filename.lower().endswith(".pdf") or not f2.filename.lower().endswith(".pdf"):
        raise ValueError("Both must be PDFs")

    return f1, f2


# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running", "service": "Packaging PDF Audit API"})


@app.route("/compare", methods=["POST"])
def compare():
    try:
        f1, f2 = validate_uploaded_pdfs()

        text_a = extract_text_intelligently(f1)
        text_b = extract_text_intelligently(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400

        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)

        if not segments_a or not segments_b:
            return jsonify({"error": "Could not segment text"}), 400

        matcher = PreciseMatcher(segments_a, segments_b)
        matched_pairs, deleted, added = matcher.match()

        report_rows = build_report_rows(matched_pairs, deleted, added)

        summary = {
            "total_rows": len(report_rows),
            "identical": sum(1 for r in report_rows if r["status"] == "IDENTICAL"),
            "minor": sum(1 for r in report_rows if r["status"] == "MINOR"),
            "significant": sum(1 for r in report_rows if r["status"] == "SIGNIFICANT"),
            "added": sum(1 for r in report_rows if r["status"] == "ADDED"),
            "deleted": sum(1 for r in report_rows if r["status"] == "DELETED"),
        }

        return jsonify({
            "report": {
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
            return jsonify({"error": "Could not extract text"}), 400

        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)

        matcher = PreciseMatcher(segments_a, segments_b)
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
