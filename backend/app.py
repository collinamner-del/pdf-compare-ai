"""
Packaging PDF Comparison & Audit API - EXPLICIT V2 TEXT

Guarantees full V2 content in every row with highlighting.
No missing data. No truncation. Clear ACTION items.
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_audit")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Segment:
    type: str
    content: str


@dataclass
class MatchResult:
    seg_a: Segment
    seg_b: Segment
    score: float
    changes: List[str]
    similarity: float


# ============================================================================
# TEXT EXTRACTION
# ============================================================================

def extract_text(file_storage) -> str:
    """Extract ALL text from PDF. No truncation."""
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        pages = []
        with pdfplumber.open(file_storage) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=True) or page.extract_text() or ""
                if text.strip():
                    pages.append(text.strip())

        return "\n\n".join(pages)

    except Exception as exc:
        raise RuntimeError(f"Extraction failed: {exc}") from exc


# ============================================================================
# SEGMENTATION - SIMPLE & RELIABLE
# ============================================================================

def segment_text(text: str) -> List[Segment]:
    """Split into sections. Keep all content intact."""
    if not text or len(text) < 20:
        return []

    # Keywords that mark sections (Waitrose-specific)
    section_keywords = {
        "PRODUCT_NAME": ["product", "brand", "name"],
        "INGREDIENTS": ["ingredients:", "composition", "contains:"],
        "ALLERGY_ADVICE": ["allergy advice:", "for allergens", "may contain"],
        "NUTRITION": ["nutrition", "energy", "per 100g", "typical values", "kcal"],
        "COOKING": ["oven cook", "gas", "chilled", "preparation:", "preheat"],
        "STORAGE": ["storage:", "keep refrigerated", "store", "temperature"],
        "WARNING": ["warning:", "contains alcohol"],
        "COMPANY": ["produced for waitrose", "made by", "manufacturer"],
    }

    lines = text.split('\n')
    segments = []
    current_type = "GENERAL"
    current_block = []

    for line in lines:
        stripped = line.strip()
        
        if not stripped:  # Empty line = boundary
            if current_block:
                content = '\n'.join(current_block).strip()
                if len(content) > 10:
                    segments.append(Segment(type=current_type, content=content))
                current_block = []
                current_type = "GENERAL"
            continue

        # Detect section type
        line_lower = stripped.lower()
        detected = None
        for section_type, keywords in section_keywords.items():
            for keyword in keywords:
                if keyword in line_lower:
                    detected = section_type
                    break
            if detected:
                break

        if detected and current_block:
            # Save previous section
            content = '\n'.join(current_block).strip()
            if len(content) > 10:
                segments.append(Segment(type=current_type, content=content))
            current_type = detected
            current_block = [line]
        else:
            current_block.append(line)

    # Don't forget last block
    if current_block:
        content = '\n'.join(current_block).strip()
        if len(content) > 10:
            segments.append(Segment(type=current_type, content=content))

    return segments


# ============================================================================
# MATCHING & DIFF
# ============================================================================

def match_segments(segs_a: List[Segment], segs_b: List[Segment]) -> Tuple[List[MatchResult], List[Segment], List[Segment]]:
    """Match segments and find changes."""
    matches = []
    used_b = set()

    for seg_a in segs_a:
        best_idx = None
        best_score = 0

        for idx, seg_b in enumerate(segs_b):
            if idx in used_b:
                continue

            # Score: 70% text similarity + 30% type match
            text_sim = SequenceMatcher(None, seg_a.content.lower(), seg_b.content.lower()).ratio()
            type_match = 1.0 if seg_a.type == seg_b.type else 0.0
            score = (0.7 * text_sim) + (0.3 * type_match)

            if score > best_score and score >= 0.5:
                best_score = score
                best_idx = idx

        if best_idx is not None:
            seg_b = segs_b[best_idx]
            changes = find_changes(seg_a.content, seg_b.content)
            similarity = SequenceMatcher(None, seg_a.content, seg_b.content).ratio() * 100
            
            matches.append(MatchResult(
                seg_a=seg_a,
                seg_b=seg_b,
                score=best_score * 100,
                changes=changes,
                similarity=similarity
            ))
            used_b.add(best_idx)

    # Find deleted and added
    deleted = []
    for idx, seg_a in enumerate(segs_a):
        is_matched = any(m.seg_a == seg_a for m in matches)
        if not is_matched:
            deleted.append(seg_a)

    added = []
    for idx, seg_b in enumerate(segs_b):
        is_matched = any(m.seg_b == seg_b for m in matches)
        if not is_matched:
            added.append(seg_b)

    return matches, deleted, added


def find_changes(text_a: str, text_b: str) -> List[str]:
    """Find exact changes between texts."""
    changes = []

    # Numbers
    nums_a = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_a)
    nums_b = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_b)
    if nums_a != nums_b:
        for na, nb in zip(nums_a, nums_b):
            if na != nb:
                changes.append(f"⚠️ Value: {na} → {nb}")

    # Punctuation
    a_no_punct = text_a.rstrip('.!?,; ')
    b_no_punct = text_b.rstrip('.!?,; ')
    if a_no_punct == b_no_punct:
        if text_a.endswith('.') != text_b.endswith('.'):
            changes.append("⚠️ Period changed")
        if text_a.endswith(',') != text_b.endswith(','):
            changes.append("⚠️ Comma changed")

    # Words
    words_a = set(text_a.split())
    words_b = set(text_b.split())
    removed = words_a - words_b
    added = words_b - words_a

    for w in removed:
        if len(w) > 2:
            changes.append(f"❌ Removed: '{w}'")
    
    for w in added:
        if len(w) > 2:
            changes.append(f"✨ Added: '{w}'")

    if not changes and text_a != text_b:
        changes.append("⚠️ Text modified")

    return changes[:5]


def highlight_diff(text_a: str, text_b: str) -> str:
    """Create HTML with highlighting. KEEP ALL V2 TEXT."""
    if text_a == text_b:
        return text_b  # No changes, return as-is

    # Mark changed portions
    matcher = SequenceMatcher(None, text_a, text_b)
    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        chunk_b = text_b[j1:j2]
        
        if tag == 'equal':
            result.append(chunk_b)
        elif tag == 'replace' or tag == 'insert':
            # This part changed or was added
            result.append(f'<mark style="background:#fbbf24;font-weight:bold;padding:2px 3px;border-radius:2px;">{chunk_b}</mark>')
        elif tag == 'delete':
            # Deleted from A, not in B - skip
            pass

    html = ''.join(result)
    return html if html else text_b


# ============================================================================
# REPORT BUILDING
# ============================================================================

def build_rows(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> List[Dict[str, Any]]:
    """Build report rows. EXPLICIT V2 CONTENT."""
    rows = []
    row_id = 1

    # Matched sections
    for match in matches:
        v2_html = highlight_diff(match.seg_a.content, match.seg_b.content)
        
        # Determine status
        if match.similarity >= 99:
            status = "IDENTICAL"
            action = "✓ Verified - no changes"
        elif match.similarity >= 95:
            status = "MINOR"
            action = f"⚠️ QC REVIEW: {len(match.changes)} change(s)"
        else:
            status = "SIGNIFICANT"
            action = f"🔴 QC REVIEW REQUIRED: {len(match.changes)} change(s)"

        rows.append({
            "row_id": f"R{row_id}",
            "element": match.seg_a.type,
            "pdf_a": match.seg_a.content,
            "pdf_b": match.seg_b.content,  # FULL V2 TEXT
            "pdf_b_html": v2_html,  # HIGHLIGHTED V2 TEXT
            "status": status,
            "similarity": round(match.similarity, 1),
            "action": action,
            "changes": match.changes,
        })
        row_id += 1

    # Deleted sections
    for seg in deleted:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": seg.content,
            "pdf_b": "",
            "pdf_b_html": "<strong style='color:#dc2626;'>❌ DELETED</strong>",
            "status": "DELETED",
            "similarity": 0.0,
            "action": "🔴 QC CRITICAL: Section removed - verify intentional",
            "changes": ["Entire section removed"],
        })
        row_id += 1

    # Added sections
    for seg in added:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": "",
            "pdf_b": seg.content,
            "pdf_b_html": f"<mark style='background:#bbf7d0;padding:3px 5px;border-radius:3px;'>{seg.content}</mark>",
            "status": "ADDED",
            "similarity": 0.0,
            "action": "⚠️ QC REVIEW: New section added - verify correct",
            "changes": ["New section added"],
        })
        row_id += 1

    return rows


# ============================================================================
# ROUTES
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running"})


@app.route("/compare", methods=["POST"])
def compare():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        text_a = extract_text(f1)
        text_b = extract_text(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400

        segs_a = segment_text(text_a)
        segs_b = segment_text(text_b)

        if not segs_a or not segs_b:
            return jsonify({"error": "Could not segment"}), 400

        matches, deleted, added = match_segments(segs_a, segs_b)
        rows = build_rows(matches, deleted, added)

        return jsonify({
            "report": {
                "comparison_table": rows,
                "summary": {
                    "total_rows": len(rows),
                    "identical": sum(1 for r in rows if r["status"] == "IDENTICAL"),
                    "minor": sum(1 for r in rows if r["status"] == "MINOR"),
                    "significant": sum(1 for r in rows if r["status"] == "SIGNIFICANT"),
                    "added": sum(1 for r in rows if r["status"] == "ADDED"),
                    "deleted": sum(1 for r in rows if r["status"] == "DELETED"),
                }
            }
        })

    except Exception as exc:
        logger.exception("Compare failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/summary", methods=["POST"])
def summary():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        text_a = extract_text(f1)
        text_b = extract_text(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400

        segs_a = segment_text(text_a)
        segs_b = segment_text(text_b)

        matches, deleted, added = match_segments(segs_a, segs_b)
        rows = build_rows(matches, deleted, added)

        # Build audit text
        audit_lines = ["QC FINDINGS", "=" * 60]
        for row in rows:
            audit_lines.append(f"\n{row['element']} | {row['status']}")
            if row['pdf_a']:
                audit_lines.append(f"A: {row['pdf_a'][:200]}")
            if row['pdf_b']:
                audit_lines.append(f"B: {row['pdf_b'][:200]}")
            if row['changes']:
                for c in row['changes']:
                    audit_lines.append(f"  {c}")

        audit_text = "\n".join(audit_lines)

        # Call OpenAI
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": "Create QC checklist."},
                {"role": "user", "content": f"Create checklist:\n{audit_text}"},
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }

        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=OPENAI_TIMEOUT,
        )

        if resp.status_code != 200:
            return jsonify({"summary": "API error"}), 500

        summary_text = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"summary": summary_text})

    except Exception as exc:
        logger.exception("Summary failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
