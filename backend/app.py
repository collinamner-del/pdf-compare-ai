"""
Packaging PDF Comparison - SPATIAL BOUNDING BOX GROUPING

Groups text blocks by spatial position (x,y coordinates) instead of
text similarity. Handles multi-column layouts perfectly.

This is a NEW file - your current app.py stays untouched.
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_audit_spatial")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Block:
    """Text block with spatial position"""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    block_type: str = "GENERAL"
    
    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2
    
    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class BlockMatch:
    block_a: Block
    block_b: Block
    changes: List[str]
    similarity: float


# ============================================================================
# TEXT EXTRACTION - GET CHARS WITH POSITIONS
# ============================================================================

def extract_text_with_positions(file_storage) -> List[Dict[str, Any]]:
    """Extract words with their bounding boxes (x0, y0, x1, y1)"""
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        words = []
        with pdfplumber.open(file_storage) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    # Extract words with bounding boxes
                    page_words = page.extract_words(x_tolerance=3, y_tolerance=3)
                    
                    if page_words:
                        for word in page_words:
                            if all(key in word for key in ["text", "x0", "y0", "x1", "y1"]):
                                words.append({
                                    "text": word["text"],
                                    "x0": float(word["x0"]),
                                    "y0": float(word["y0"]),
                                    "x1": float(word["x1"]),
                                    "y1": float(word["y1"]),
                                    "page": page_num,
                                })
                except Exception as page_exc:
                    logger.warning(f"Page {page_num} extraction issue: {page_exc}")
                    continue

        if not words:
            raise RuntimeError("No text extracted from PDFs")

        return words

    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc


# ============================================================================
# SPATIAL CLUSTERING - GROUP BY POSITION
# ============================================================================

def cluster_blocks_by_position(chars: List[Dict[str, Any]]) -> List[Block]:
    """
    Group characters into blocks using spatial clustering.
    Characters close together (same line, nearby columns) = same block.
    Handles edge cases and malformed PDFs gracefully.
    """
    if not chars or len(chars) < 2:
        return []

    blocks = []
    used = set()

    # Sort by Y position (top to bottom), then X (left to right)
    try:
        sorted_chars = sorted(chars, key=lambda c: (round(c.get("y0", 0), 1), c.get("x0", 0)))
    except Exception as e:
        logger.warning(f"Sorting failed: {e}. Using unsorted list.")
        sorted_chars = chars

    for idx, char in enumerate(sorted_chars):
        if idx in used:
            continue

        # Validate char has required fields
        if not all(key in char for key in ["text", "x0", "y0", "x1", "y1"]):
            used.add(idx)
            continue

        # Start a new block with this character
        block_chars = [char]
        used.add(idx)
        
        # Find all characters that belong to this block
        y_min = char.get("y0", 0) - 5
        y_max = char.get("y1", 0) + 5
        last_x1 = char.get("x1", 0)

        for idx2, char2 in enumerate(sorted_chars):
            if idx2 in used:
                continue
            
            # Validate char2
            if not all(key in char2 for key in ["text", "x0", "y0", "x1", "y1"]):
                continue
            
            # Same line?
            char2_y0 = char2.get("y0", 0)
            char2_y1 = char2.get("y1", 0)
            if y_min <= char2_y0 <= y_max or y_min <= char2_y1 <= y_max:
                # And roughly same X area (same horizontal zone)?
                char2_x0 = char2.get("x0", 0)
                if abs(char2_x0 - last_x1) < 50:  # 50px tolerance
                    block_chars.append(char2)
                    used.add(idx2)
                    last_x1 = char2.get("x1", 0)

        # Create block from grouped characters
        if block_chars:
            text = " ".join([c.get("text", "") for c in block_chars]).strip()
            
            if not text:
                continue
            
            try:
                x_coords = [c.get("x0", 0) for c in block_chars] + [c.get("x1", 0) for c in block_chars]
                y_coords = [c.get("y0", 0) for c in block_chars] + [c.get("y1", 0) for c in block_chars]

                block = Block(
                    text=text,
                    x0=min(x_coords) if x_coords else 0,
                    y0=min(y_coords) if y_coords else 0,
                    x1=max(x_coords) if x_coords else 0,
                    y1=max(y_coords) if y_coords else 0,
                    block_type=detect_block_type(text),
                )
                
                # Only keep blocks with meaningful content
                if len(text.strip()) > 5:
                    blocks.append(block)
            except Exception as e:
                logger.warning(f"Block creation error: {e}")
                continue

    return blocks


def detect_block_type(text: str) -> str:
    """Detect what type of section this block is"""
    text_lower = text.lower()
    
    if any(kw in text_lower for kw in ["ingredient", "composition"]):
        return "INGREDIENTS"
    elif any(kw in text_lower for kw in ["nutrition", "energy", "per 100"]):
        return "NUTRITION"
    elif any(kw in text_lower for kw in ["allerg", "may contain", "nut", "milk"]):
        return "ALLERGENS"
    elif any(kw in text_lower for kw in ["stor", "keep", "best before", "use by"]):
        return "STORAGE"
    elif any(kw in text_lower for kw in ["instruction", "direction", "cook", "prepare"]):
        return "INSTRUCTIONS"
    elif any(kw in text_lower for kw in ["made by", "produced", "manufacturer"]):
        return "COMPANY"
    elif len(text) < 30:
        return "PRODUCT_NAME"
    
    return "GENERAL"


# ============================================================================
# MATCHING - BY POSITION (X, Y COORDINATES)
# ============================================================================

def match_blocks_by_position(blocks_a: List[Block], blocks_b: List[Block]) -> Tuple[List[BlockMatch], List[Block], List[Block]]:
    """
    Match blocks across PDFs using spatial position.
    Blocks at similar Y position = match.
    """
    matches = []
    used_b = set()

    for block_a in blocks_a:
        best_idx_b = None
        best_distance = float('inf')

        for idx_b, block_b in enumerate(blocks_b):
            if idx_b in used_b:
                continue

            # Distance metric: Y position is primary (vertical stack)
            # X position is secondary (left/right columns)
            y_distance = abs(block_a.center_y - block_b.center_y)
            x_distance = abs(block_a.center_x - block_b.center_x) * 0.2  # Weight less

            distance = y_distance + x_distance

            # Prefer matches within same general area (< 50 pixels Y distance)
            if distance < best_distance and y_distance < 50:
                best_distance = distance
                best_idx_b = idx_b

        if best_idx_b is not None:
            block_b = blocks_b[best_idx_b]
            changes = find_changes(block_a.text, block_b.text)
            similarity = text_similarity(block_a.text, block_b.text)
            
            matches.append(BlockMatch(
                block_a=block_a,
                block_b=block_b,
                changes=changes,
                similarity=similarity,
            ))
            used_b.add(best_idx_b)

    # Find unmatched (deleted/added)
    deleted = []
    for block_a in blocks_a:
        if not any(m.block_a == block_a for m in matches):
            deleted.append(block_a)

    added = []
    for idx_b, block_b in enumerate(blocks_b):
        if idx_b not in used_b:
            added.append(block_b)

    return matches, deleted, added


def text_similarity(text_a: str, text_b: str) -> float:
    """Calculate similarity percentage"""
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
    return round(ratio * 100, 1)


def find_changes(text_a: str, text_b: str) -> List[str]:
    """Find exact changes between texts"""
    changes = []

    # Numbers
    nums_a = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_a)
    nums_b = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_b)
    if nums_a != nums_b:
        for na, nb in zip(nums_a, nums_b):
            if na != nb:
                changes.append(f"⚠️ Value: {na} → {nb}")

    # Punctuation
    a_clean = text_a.rstrip('.!?,; ')
    b_clean = text_b.rstrip('.!?,; ')
    if a_clean == b_clean and text_a != text_b:
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


def highlight_changes(text_a: str, text_b: str) -> str:
    """Create HTML highlighting differences"""
    from difflib import SequenceMatcher
    
    if text_a == text_b:
        return text_b

    matcher = SequenceMatcher(None, text_a, text_b)
    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        chunk = text_b[j1:j2]
        
        if tag == 'equal':
            result.append(chunk)
        elif tag == 'replace' or tag == 'insert':
            result.append(f'<mark style="background:#fbbf24;font-weight:bold;padding:2px 3px;">{chunk}</mark>')
        elif tag == 'delete':
            pass

    return ''.join(result) if result else text_b


# ============================================================================
# REPORT BUILDING
# ============================================================================

def build_report_rows(matches: List[BlockMatch], deleted: List[Block], added: List[Block]) -> List[Dict[str, Any]]:
    """Build report rows for display"""
    rows = []
    row_id = 1

    for match in matches:
        if match.similarity >= 98:
            status = "IDENTICAL"
            action = "✓ No action needed"
        elif match.similarity >= 85:
            status = "MINOR"
            action = f"⚠️ Review: {len(match.changes)} change(s)"
        else:
            status = "SIGNIFICANT"
            action = f"🔴 CHECK: {len(match.changes)} significant change(s)"

        html = highlight_changes(match.block_a.text, match.block_b.text)

        rows.append({
            "row_id": f"R{row_id}",
            "element": match.block_a.block_type,
            "pdf_a": match.block_a.text,
            "pdf_b": match.block_b.text,
            "pdf_b_html": html,
            "status": status,
            "similarity": match.similarity,
            "action": action,
            "changes": match.changes,
        })
        row_id += 1

    for block in deleted:
        rows.append({
            "row_id": f"R{row_id}",
            "element": block.block_type,
            "pdf_a": block.text,
            "pdf_b": "",
            "pdf_b_html": "<strong style='color:#dc2626;'>❌ DELETED</strong>",
            "status": "DELETED",
            "similarity": 0.0,
            "action": "❌ VERIFY: Section removed",
            "changes": ["Entire section removed"],
        })
        row_id += 1

    for block in added:
        rows.append({
            "row_id": f"R{row_id}",
            "element": block.block_type,
            "pdf_a": "",
            "pdf_b": block.text,
            "pdf_b_html": f"<mark style='background:#bbf7d0;padding:3px 5px;'>{block.text}</mark>",
            "status": "ADDED",
            "similarity": 0.0,
            "action": "✓ NEW: Verify content correct",
            "changes": ["New section added"],
        })
        row_id += 1

    return rows


# ============================================================================
# ROUTES
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - SPATIAL GROUPING VERSION"})


@app.route("/compare", methods=["POST"])
def compare():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        # Extract with positions
        chars_a = extract_text_with_positions(f1)
        chars_b = extract_text_with_positions(f2)

        if not chars_a or not chars_b:
            return jsonify({"error": "Could not extract text"}), 400

        # Cluster into blocks by spatial position
        blocks_a = cluster_blocks_by_position(chars_a)
        blocks_b = cluster_blocks_by_position(chars_b)

        if not blocks_a or not blocks_b:
            return jsonify({"error": "Could not create blocks"}), 400

        # Match by position
        matches, deleted, added = match_blocks_by_position(blocks_a, blocks_b)

        # Build rows
        rows = build_report_rows(matches, deleted, added)

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

        chars_a = extract_text_with_positions(f1)
        chars_b = extract_text_with_positions(f2)

        if not chars_a or not chars_b:
            return jsonify({"error": "Could not extract text"}), 400

        blocks_a = cluster_blocks_by_position(chars_a)
        blocks_b = cluster_blocks_by_position(chars_b)

        matches, deleted, added = match_blocks_by_position(blocks_a, blocks_b)
        rows = build_report_rows(matches, deleted, added)

        # Build audit text
        audit_lines = ["QC FINDINGS"]
        for row in rows:
            audit_lines.append(f"\n{row['element']} | {row['status']}")
            if row['pdf_a']:
                audit_lines.append(f"A: {row['pdf_a']}")
            if row['pdf_b']:
                audit_lines.append(f"B: {row['pdf_b']}")
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
                {"role": "user", "content": f"QC Checklist from audit:\n{audit_text}"},
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
            return jsonify({"summary": "Could not generate summary"}), 500

        summary_text = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"summary": summary_text})

    except Exception as exc:
        logger.exception("Summary failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
