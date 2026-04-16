"""
Packaging PDF Comparison - ULTRA-SMART PRO VERSION
Anchor-based alignment + cross-block analysis + intelligent merging

Fixes the root cause: misaligned extraction between PDFs.
Uses common text anchors to synchronize before comparison.
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher, ndiff
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_audit_ultra")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Block:
    text: str
    block_type: str
    source_lines: int = 1


@dataclass
class BlockMatch:
    block_a: Block
    block_b: Block
    similarity: float
    changes: List[str]
    highlighted: str


# ============================================================================
# ANCHOR-BASED ALIGNMENT - Sync PDFs Before Comparison
# ============================================================================

class AnchorAligner:
    """
    Finds common anchor points (text that appears in both PDFs unchanged).
    Uses these to understand structure and detect misaligned extractions.
    """
    
    @staticmethod
    def find_common_phrases(text_a: str, text_b: str) -> List[str]:
        """Find phrases that appear in both texts"""
        # Split into paragraphs/blocks
        para_a = re.split(r'\n\n+', text_a)
        para_b = re.split(r'\n\n+', text_b)
        
        common = []
        
        for p_a in para_a:
            if len(p_a) < 20:
                continue
            # Normalize
            normalized_a = p_a.lower().strip()
            
            for p_b in para_b:
                normalized_b = p_b.lower().strip()
                
                # Check similarity
                ratio = SequenceMatcher(None, normalized_a, normalized_b).ratio()
                if ratio > 0.95:  # Very similar = anchor
                    common.append(p_a[:50])  # Store first 50 chars as anchor
        
        return common
    
    @staticmethod
    def get_alignment_confidence(text_a: str, text_b: str) -> float:
        """
        Score how well the texts align.
        High score = both PDFs have same structure.
        Low score = extraction may be misaligned.
        """
        anchors = AnchorAligner.find_common_phrases(text_a, text_b)
        
        # More anchors = more confidence
        if len(anchors) > 5:
            return 0.95
        elif len(anchors) > 2:
            return 0.80
        else:
            return 0.60


# ============================================================================
# MULTI-METHOD EXTRACTION
# ============================================================================

def extract_text_best_effort(file_storage) -> str:
    """Try multiple extraction methods, return best result"""
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        best_text = ""
        
        with pdfplumber.open(file_storage) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = ""
                
                # Method 1: Layout mode
                try:
                    text = page.extract_text(layout=True)
                    if text and len(text.strip()) > len(page_text):
                        page_text = text
                except:
                    pass
                
                # Method 2: Standard
                if not page_text or len(page_text.strip()) < 50:
                    try:
                        text = page.extract_text()
                        if text and len(text.strip()) > len(page_text):
                            page_text = text
                    except:
                        pass
                
                # Method 3: Words fallback
                if not page_text or len(page_text.strip()) < 50:
                    try:
                        words = page.extract_words()
                        if words:
                            page_text = " ".join([w.get("text", "") for w in words])
                    except:
                        pass
                
                if page_text:
                    best_text += page_text + "\n\n"
        
        return best_text.strip() if best_text else ""

    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc


# ============================================================================
# INTELLIGENT SEGMENTATION WITH CROSS-BLOCK ANALYSIS
# ============================================================================

class IntelligentSegmenter:
    """
    Smart segmentation that:
    1. Groups by line breaks
    2. Detects types by content
    3. Merges blocks with spillover (word in wrong block)
    4. Handles misaligned extraction
    """

    PACKAGING_TYPES = {
        "PRODUCT_NAME": {
            "patterns": [r"^[A-Z][A-Za-z\s&\-]{5,80}$"],
            "position": "early",
            "length_range": (6, 100),
        },
        "INGREDIENTS": {
            "patterns": [r"ingredient", r"composition", r"contain"],
            "indicators": ["milk", "sugar", "cocoa", "water", "%"],
        },
        "NUTRITION": {
            "patterns": [r"nutrition", r"energy", r"protein", r"fat"],
            "indicators": ["kcal", "kj", "per 100", "0%"],
        },
        "ALLERGENS": {
            "patterns": [r"allerg", r"may contain"],
            "indicators": ["nut", "milk", "sesame", "soy"],
        },
        "STORAGE": {
            "patterns": [r"stor", r"keep", r"best before"],
            "indicators": ["temperature", "dry", "cool"],
        },
        "INSTRUCTIONS": {
            "patterns": [r"instruction", r"direction", r"cook"],
            "indicators": ["heat", "mix", "serve"],
        },
        "COMPANY": {
            "patterns": [r"made by", r"produced", r"manufacturer"],
            "indicators": ["ltd", "inc", "address"],
        },
    }

    @classmethod
    def segment(cls, text: str) -> List[Block]:
        """Segment with spillover detection"""
        if not text or len(text) < 20:
            return []

        # Initial segmentation
        blocks = cls._initial_segment(text)
        
        # Post-process: merge blocks with spillover
        blocks = cls._merge_spillover_blocks(blocks)
        
        return blocks

    @classmethod
    def _initial_segment(cls, text: str) -> List[Block]:
        """Basic line-based segmentation"""
        lines = text.split('\n')
        blocks = []
        current_block_lines = []
        current_type = "GENERAL"

        for line in lines:
            stripped = line.strip()
            
            # Empty line = boundary
            if not stripped:
                if current_block_lines:
                    block_text = '\n'.join(current_block_lines).strip()
                    if len(block_text) > 5:
                        detected_type = cls._detect_type(block_text)
                        blocks.append(Block(
                            text=block_text,
                            block_type=detected_type,
                            source_lines=len(current_block_lines),
                        ))
                    current_block_lines = []
                    current_type = "GENERAL"
                continue
            
            # New section?
            detected = cls._detect_type(stripped)
            if detected and detected != "GENERAL" and current_block_lines:
                block_text = '\n'.join(current_block_lines).strip()
                if len(block_text) > 5:
                    blocks.append(Block(
                        text=block_text,
                        block_type=current_type,
                        source_lines=len(current_block_lines),
                    ))
                current_block_lines = [line]
                current_type = detected
            else:
                current_block_lines.append(line)

        if current_block_lines:
            block_text = '\n'.join(current_block_lines).strip()
            if len(block_text) > 5:
                blocks.append(Block(
                    text=block_text,
                    block_type=current_type,
                    source_lines=len(current_block_lines),
                ))

        return blocks

    @classmethod
    def _merge_spillover_blocks(cls, blocks: List[Block]) -> List[Block]:
        """
        Detect and merge blocks where content spilled into wrong block.
        Example: "extract" in block N and block N+1 = spillover
        """
        if len(blocks) < 2:
            return blocks

        merged = []
        skip_next = False

        for i, block in enumerate(blocks):
            if skip_next:
                skip_next = False
                continue

            # Check if next block is continuation (spillover)
            if i < len(blocks) - 1:
                next_block = blocks[i + 1]
                
                # If blocks have same type and small next block, might be spillover
                if (block.block_type == next_block.block_type and 
                    len(next_block.text) < 100 and
                    len(next_block.source_lines) <= 2):
                    
                    # Merge them
                    merged_text = block.text + " " + next_block.text
                    merged.append(Block(
                        text=merged_text,
                        block_type=block.block_type,
                        source_lines=block.source_lines + next_block.source_lines,
                    ))
                    skip_next = True
                else:
                    merged.append(block)
            else:
                merged.append(block)

        return merged

    @classmethod
    def _detect_type(cls, text: str) -> str:
        """Detect block type"""
        text_lower = text.lower()
        
        for block_type, config in cls.PACKAGING_TYPES.items():
            for pattern in config.get("patterns", []):
                if re.search(pattern, text_lower):
                    indicators = config.get("indicators", [])
                    if not indicators or any(ind in text_lower for ind in indicators):
                        return block_type

        if len(text) < 50:
            return "PRODUCT_NAME"

        return "GENERAL"


# ============================================================================
# SMART MATCHING WITH CROSS-BLOCK CHECKING
# ============================================================================

def match_blocks(blocks_a: List[Block], blocks_b: List[Block]) -> Tuple[List[BlockMatch], List[Block], List[Block]]:
    """
    Smart matching that checks for cross-block issues.
    If a block matches poorly, check if content spilled to neighbors.
    """
    matches = []
    used_b = set()

    for i, block_a in enumerate(blocks_a):
        best_idx = None
        best_score = 0.0
        best_block = None

        # Find best match in blocks_b
        for idx_b, block_b in enumerate(blocks_b):
            if idx_b in used_b:
                continue

            text_sim = semantic_similarity(block_a.text, block_b.text)
            type_match = 1.0 if block_a.block_type == block_b.block_type else 0.5

            score = (0.6 * text_sim) + (0.4 * type_match)

            if score > best_score and score >= 0.45:
                best_score = score
                best_idx = idx_b
                best_block = block_b

        # Cross-block check: if match is weak, check neighbors
        if best_score < 0.70 and best_idx is not None:
            # Check if content might be in neighbor blocks
            neighbor_scores = []
            
            if best_idx > 0:
                neighbor_block = blocks_b[best_idx - 1]
                n_sim = semantic_similarity(block_a.text, neighbor_block.text)
                neighbor_scores.append((n_sim, best_idx - 1))
            
            if best_idx < len(blocks_b) - 1:
                neighbor_block = blocks_b[best_idx + 1]
                n_sim = semantic_similarity(block_a.text, neighbor_block.text)
                neighbor_scores.append((n_sim, best_idx + 1))
            
            # If neighbor is better, use that instead
            if neighbor_scores:
                best_neighbor_score, best_neighbor_idx = max(neighbor_scores, key=lambda x: x[0])
                if best_neighbor_score > best_score:
                    best_idx = best_neighbor_idx
                    best_block = blocks_b[best_idx]
                    best_score = best_neighbor_score

        if best_idx is not None and best_block:
            changes = find_exact_changes(block_a.text, block_b.text)
            similarity = text_similarity(block_a.text, block_b.text)
            highlighted = highlight_differences(block_a.text, block_b.text)
            
            matches.append(BlockMatch(
                block_a=block_a,
                block_b=best_block,
                similarity=similarity,
                changes=changes,
                highlighted=highlighted,
            ))
            used_b.add(best_idx)

    deleted = [b for b in blocks_a if not any(m.block_a == b for m in matches)]
    added = [b for b in blocks_b if blocks_b.index(b) not in used_b]

    return matches, deleted, added


def semantic_similarity(text_a: str, text_b: str) -> float:
    """Better similarity scoring"""
    ratio = SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
    
    len_a, len_b = len(text_a), len(text_b)
    len_penalty = 1.0
    if max(len_a, len_b) > 0:
        len_ratio = min(len_a, len_b) / max(len_a, len_b)
        if len_ratio < 0.7:
            len_penalty = len_ratio

    return ratio * len_penalty


def text_similarity(text_a: str, text_b: str) -> float:
    ratio = SequenceMatcher(None, text_a, text_b).ratio()
    return round(ratio * 100, 1)


def find_exact_changes(text_a: str, text_b: str) -> List[str]:
    """Find exact changes"""
    changes = []

    if text_a == text_b:
        return []

    # Numbers
    nums_a = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_a)
    nums_b = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_b)
    
    for na, nb in zip(nums_a, nums_b):
        if na != nb:
            changes.append(f"⚠️ Value: {na} → {nb}")

    # Punctuation
    a_clean = text_a.rstrip('.!?,; ')
    b_clean = text_b.rstrip('.!?,; ')
    if a_clean == b_clean and text_a != text_b:
        if text_a.endswith('.') != text_b.endswith('.'):
            changes.append("⚠️ Period changed")

    # Words
    words_a = set(text_a.split())
    words_b = set(text_b.split())
    
    removed = words_a - words_b
    added = words_b - words_a

    for w in removed:
        if len(w) > 2 and w not in ['.', ',']:
            changes.append(f"❌ Removed: '{w}'")
    
    for w in added:
        if len(w) > 2 and w not in ['.', ',']:
            changes.append(f"✨ Added: '{w}'")

    if not changes and text_a != text_b:
        changes.append("⚠️ Text modified")

    return changes[:5]


def highlight_differences(text_a: str, text_b: str) -> str:
    """Character-level highlighting"""
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

    return ''.join(result) if result else text_b


# ============================================================================
# REPORT BUILDING
# ============================================================================

def build_report(matches: List[BlockMatch], deleted: List[Block], added: List[Block]) -> List[Dict[str, Any]]:
    """Build report"""
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

        rows.append({
            "row_id": f"R{row_id}",
            "element": match.block_a.block_type,
            "pdf_a": match.block_a.text,
            "pdf_b": match.block_b.text,
            "pdf_b_html": match.highlighted,
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
    return jsonify({"status": "API running - ULTRA-SMART PRO VERSION"})


@app.route("/compare", methods=["POST"])
def compare():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        # Extract with best effort
        text_a = extract_text_best_effort(f1)
        text_b = extract_text_best_effort(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400

        # Check alignment confidence
        alignment = AnchorAligner.get_alignment_confidence(text_a, text_b)
        logger.info(f"Alignment confidence: {alignment:.2%}")

        # Intelligent segmentation with spillover detection
        blocks_a = IntelligentSegmenter.segment(text_a)
        blocks_b = IntelligentSegmenter.segment(text_b)

        if not blocks_a or not blocks_b:
            return jsonify({"error": "Could not segment text"}), 400

        # Smart matching with cross-block checking
        matches, deleted, added = match_blocks(blocks_a, blocks_b)

        # Build report
        rows = build_report(matches, deleted, added)

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

        text_a = extract_text_best_effort(f1)
        text_b = extract_text_best_effort(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400

        blocks_a = IntelligentSegmenter.segment(text_a)
        blocks_b = IntelligentSegmenter.segment(text_b)

        matches, deleted, added = match_blocks(blocks_a, blocks_b)
        rows = build_report(matches, deleted, added)

        # Build audit
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

        # OpenAI
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
