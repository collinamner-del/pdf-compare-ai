"""
Packaging PDF Comparison - STABLE VERSION
Back to simple, reliable extraction + Infallible Mode
No over-engineered table detection - just works.
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
logger = logging.getLogger("pdf_audit_stable")

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
# TEXT EXTRACTION - SIMPLE & RELIABLE
# ============================================================================

def extract_text(file_storage) -> str:
    """Extract ALL text from PDF. Simple, no tricks."""
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
        
        if not stripped:
            if current_block:
                content = '\n'.join(current_block).strip()
                if len(content) > 10:
                    segments.append(Segment(type=current_type, content=content))
                current_block = []
                current_type = "GENERAL"
            continue

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
            content = '\n'.join(current_block).strip()
            if len(content) > 10:
                segments.append(Segment(type=current_type, content=content))
            current_type = detected
            current_block = [line]
        else:
            current_block.append(line)

    if current_block:
        content = '\n'.join(current_block).strip()
        if len(content) > 10:
            segments.append(Segment(type=current_type, content=content))

    return segments


# ============================================================================
# MATCHING
# ============================================================================

def match_segments(segs_a: List[Segment], segs_b: List[Segment]) -> Tuple[List[MatchResult], List[Segment], List[Segment]]:
    """Match segments - simple and reliable."""
    matches = []
    used_b = set()

    for seg_a in segs_a:
        best_idx = None
        best_score = 0

        for idx, seg_b in enumerate(segs_b):
            if idx in used_b:
                continue

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

    deleted = [s for s in segs_a if not any(m.seg_a == s for m in matches)]
    added = [segs_b[i] for i in range(len(segs_b)) if i not in used_b]

    return matches, deleted, added


def find_changes(text_a: str, text_b: str) -> List[str]:
    """Find exact changes."""
    changes = []

    if text_a == text_b:
        return []

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
        if len(w) > 2 and w not in ['.', ',', '!', '?']:
            changes.append(f"❌ Removed: '{w}'")
    
    for w in added:
        if len(w) > 2 and w not in ['.', ',', '!', '?']:
            changes.append(f"✨ Added: '{w}'")

    if not changes and text_a != text_b:
        changes.append("⚠️ Text modified")

    return changes[:5]


def highlight_diff(text_a: str, text_b: str) -> str:
    """Highlight differences between texts."""
    if text_a == text_b:
        return text_b

    matcher = SequenceMatcher(None, text_a, text_b)
    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        chunk_b = text_b[j1:j2]
        
        if tag == 'equal':
            result.append(chunk_b)
        elif tag == 'replace' or tag == 'insert':
            result.append(f'<mark style="background:#fbbf24;font-weight:bold;padding:2px 3px;border-radius:2px;">{chunk_b}</mark>')
        elif tag == 'delete':
            pass

    html = ''.join(result)
    return html if html else text_b


# ============================================================================
# SMART CONTENT RECONCILIATION - Auto-Fix OCR Issues
# ============================================================================

def reconcile_misaligned_content(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Global reconciliation: If deleted content appears as added ANYWHERE in report,
    it's an extraction issue - merge them back together.
    """
    deleted_rows = [i for i, r in enumerate(rows) if r["status"] == "DELETED"]
    added_rows = [i for i, r in enumerate(rows) if r["status"] == "ADDED"]
    
    reconciled = set()
    
    # For each deleted row, check ALL added rows
    for del_idx in deleted_rows:
        if del_idx in reconciled:
            continue
            
        deleted_content = rows[del_idx]["pdf_a"].lower().strip()
        deleted_words = set(w for w in deleted_content.split() if len(w) > 2)
        
        if not deleted_words:
            continue
        
        for add_idx in added_rows:
            if add_idx in reconciled:
                continue
            
            added_content = rows[add_idx]["pdf_b"].lower().strip() if rows[add_idx]["pdf_b"] else ""
            added_words = set(w for w in added_content.split() if len(w) > 2)
            
            # Calculate overlap
            if not added_words:
                continue
                
            overlap = len(deleted_words & added_words)
            overlap_ratio = overlap / max(len(deleted_words), len(added_words))
            
            # HIGH overlap (>70%) = same block, just moved/split
            if overlap_ratio > 0.70:
                # Mark both as reconciled (they're the same thing)
                rows[del_idx]["status"] = "RECONCILED"
                rows[del_idx]["action"] = "✓ Content reconciled (same in both versions)"
                rows[del_idx]["changes"] = []
                
                rows[add_idx]["status"] = "RECONCILED"
                rows[add_idx]["action"] = "✓ Content reconciled (same in both versions)"
                rows[add_idx]["changes"] = []
                
                reconciled.add(del_idx)
                reconciled.add(add_idx)
                break
    
    # Remove RECONCILED rows (they're not real issues)
    result_rows = [r for r in rows if r["status"] != "RECONCILED"]
    
    return result_rows

def build_rows(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> List[Dict[str, Any]]:
    """
    Build report - INFALLIBLE MODE.
    Show ONLY blocks with changes. Hide perfect blocks.
    """
    rows = []
    row_id = 1

    for match in matches:
        v2_html = highlight_diff(match.seg_a.content, match.seg_b.content)
        
        # SKIP if 100% identical
        if match.similarity >= 99.9:
            continue
        
        # ANYTHING LESS = QC REVIEW
        status = "CHANGED"
        action = f"🔴 QC REVIEW REQUIRED: {len(match.changes)} change(s)"
        
        rows.append({
            "row_id": f"R{row_id}",
            "element": match.seg_a.type,
            "pdf_a": match.seg_a.content,
            "pdf_b": match.seg_b.content,
            "pdf_b_html": v2_html,
            "status": status,
            "similarity": round(match.similarity, 1),
            "action": action,
            "changes": match.changes,
        })
        row_id += 1

    for seg in deleted:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": seg.content,
            "pdf_b": "",
            "pdf_b_html": "<strong style='color:#dc2626;'>❌ DELETED</strong>",
            "status": "DELETED",
            "similarity": 0.0,
            "action": "🔴 QC CRITICAL: Section removed",
            "changes": ["Entire section deleted"],
        })
        row_id += 1

    for seg in added:
        rows.append({
            "row_id": f"R{row_id}",
            "element": seg.type,
            "pdf_a": "",
            "pdf_b": seg.content,
            "pdf_b_html": f"<mark style='background:#bbf7d0;padding:3px 5px;border-radius:3px;'>{seg.content}</mark>",
            "status": "ADDED",
            "similarity": 0.0,
            "action": "🔴 QC REVIEW: New section added",
            "changes": ["New section"],
        })
        row_id += 1

    return rows


# ============================================================================
# ROUTES
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - STABLE VERSION"})


@app.route("/compare", methods=["POST"])
def compare():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        text_a = extract_text(f1)
        text_b = extract_text(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400

        segs_a = segment_text(text_a)
        segs_b = segment_text(text_b)

        if not segs_a or not segs_b:
            return jsonify({"error": "Could not segment text"}), 400

        matches, deleted, added = match_segments(segs_a, segs_b)
        rows = build_rows(matches, deleted, added)
        
        # Smart QC: auto-fix OCR misalignment
        rows = reconcile_misaligned_content(rows)

        return jsonify({
            "report": {
                "comparison_table": rows,
                "summary": {
                    "total_rows": len(rows),
                    "changed": sum(1 for r in rows if r["status"] == "CHANGED"),
                    "added": sum(1 for r in rows if r["status"] == "ADDED"),
                    "deleted": sum(1 for r in rows if r["status"] == "DELETED"),
                    "blocks_checked": len(matches),
                    "blocks_perfect": sum(1 for m in matches if m.similarity >= 99.9),
                    "auto_fixed": sum(1 for r in rows if r.get("action", "").startswith("✓ AUTO-FIXED")),
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
        
        # Smart QC: auto-fix OCR misalignment
        rows = reconcile_misaligned_content(rows)

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

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": "Create QC checklist."},
                {"role": "user", "content": f"QC Checklist:\n{audit_text}"},
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
            return jsonify({"summary": "Summary generation failed"}), 500

        summary_text = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"summary": summary_text})

    except Exception as exc:
        logger.exception("Summary failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
