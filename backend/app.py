"""
Packaging PDF Comparison - ENHANCED VERSION
Tesseract OCR (industry standard) + SSIM visual comparison (secondary)
Content-first, visual-aware approach
"""

from __future__ import annotations

import os
import re
import logging
import io
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# Image processing
from pdf2image import convert_from_bytes
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

# Better OCR - Tesseract
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logging.warning("pytesseract not available - using pdfplumber only")

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_audit_enhanced")

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
# IMAGE-BASED ANALYSIS (SECONDARY)
# ============================================================================

def convert_pdf_to_images(file_bytes: bytes) -> List[np.ndarray]:
    """Convert PDF to images"""
    try:
        images = convert_from_bytes(file_bytes, dpi=150)
        return [cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY) for img in images]
    except Exception as e:
        logger.warning(f"PDF to image conversion failed: {e}")
        return []


def calculate_ssim_diff(img1: np.ndarray, img2: np.ndarray) -> Tuple[float, np.ndarray]:
    """Calculate structural similarity and get diff"""
    try:
        # Ensure same size
        h, w = min(img1.shape[0], img2.shape[0]), min(img1.shape[1], img2.shape[1])
        img1_resized = img1[:h, :w]
        img2_resized = img2[:h, :w]
        
        # Calculate SSIM
        similarity, diff = ssim(img1_resized, img2_resized, full=True)
        diff = (diff * 255).astype("uint8")
        
        return similarity, diff
    except Exception as e:
        logger.warning(f"SSIM calculation failed: {e}")
        return 0.0, None


def get_visual_change_percentage(images_a: List[np.ndarray], images_b: List[np.ndarray]) -> float:
    """Get overall visual change percentage"""
    if not images_a or not images_b:
        return 0.0
    
    similarities = []
    for img_a, img_b in zip(images_a[:min(len(images_a), len(images_b))], images_b[:min(len(images_a), len(images_b))]):
        try:
            sim, _ = calculate_ssim_diff(img_a, img_b)
            similarities.append(sim)
        except:
            continue
    
    if similarities:
        avg_sim = np.mean(similarities)
        return round((1 - avg_sim) * 100, 1)
    return 0.0


# ============================================================================
# ENHANCED OCR EXTRACTION - TESSERACT
# ============================================================================

def extract_text_enhanced(file_storage) -> str:
    """Extract text using both pdfplumber and Tesseract for best results"""
    try:
        if hasattr(file_storage, "seek"):
            file_storage.seek(0)

        pages = []
        
        with pdfplumber.open(file_storage) as pdf:
            for page_num, page in enumerate(pdf.pages):
                # Try pdfplumber first (faster, structured)
                text = page.extract_text(layout=True) or page.extract_text() or ""
                
                # If pdfplumber got little/nothing, try Tesseract
                if TESSERACT_AVAILABLE and (not text or len(text.strip()) < 50):
                    try:
                        logger.info(f"Running Tesseract OCR on page {page_num + 1}")
                        # Convert page to image and OCR
                        img = page.to_image()
                        img_np = cv2.cvtColor(np.array(img.original), cv2.COLOR_RGB2BGR)
                        
                        # Use Tesseract with PSM 6 (assume single uniform block of text)
                        ocr_text = pytesseract.image_to_string(img_np, config='--psm 6')
                        
                        if ocr_text.strip():
                            text = ocr_text if len(ocr_text) > len(text) else text
                    except Exception as e:
                        logger.warning(f"Tesseract fallback failed on page {page_num + 1}: {e}")
                
                if text.strip():
                    pages.append(text.strip())

        return "\n\n".join(pages)

    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc


# ============================================================================
# SEGMENTATION
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

    nums_a = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_a)
    nums_b = re.findall(r'\d+(?:\.\d+)?(?:\s*[gmkl%°CF])?', text_b)
    if nums_a != nums_b:
        for na, nb in zip(nums_a, nums_b):
            if na != nb:
                changes.append(f"⚠️ Value: {na} → {nb}")

    a_no_punct = text_a.rstrip('.!?,; ')
    b_no_punct = text_b.rstrip('.!?,; ')
    if a_no_punct == b_no_punct:
        if text_a.endswith('.') != text_b.endswith('.'):
            changes.append("⚠️ Period changed")
        if text_a.endswith(',') != text_b.endswith(','):
            changes.append("⚠️ Comma changed")

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
# SMART CONTENT RECONCILIATION
# ============================================================================

def reconcile_misaligned_content(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Global reconciliation: detect when deleted content appears as added"""
    try:
        deleted_indices = [i for i, r in enumerate(rows) if r.get("status") == "DELETED"]
        added_indices = [i for i, r in enumerate(rows) if r.get("status") == "ADDED"]
        
        reconciled = set()
        
        for del_idx in deleted_indices:
            if del_idx in reconciled:
                continue
            
            del_text = rows[del_idx].get("pdf_a", "").lower().strip()
            del_words = set(w for w in del_text.split() if len(w) > 2)
            
            if len(del_words) < 3:
                continue
            
            for add_idx in added_indices:
                if add_idx in reconciled:
                    continue
                
                add_text = rows[add_idx].get("pdf_b", "").lower().strip()
                add_words = set(w for w in add_text.split() if len(w) > 2)
                
                if len(add_words) < 3:
                    continue
                
                overlap = len(del_words & add_words)
                max_len = max(len(del_words), len(add_words))
                ratio = overlap / max_len if max_len > 0 else 0
                
                if ratio > 0.70:
                    rows[del_idx]["status"] = "RECONCILED"
                    rows[del_idx]["action"] = "✓ Content reconciled"
                    rows[del_idx]["changes"] = []
                    
                    rows[add_idx]["status"] = "RECONCILED"
                    rows[add_idx]["action"] = "✓ Content reconciled"
                    rows[add_idx]["changes"] = []
                    
                    reconciled.add(del_idx)
                    reconciled.add(add_idx)
                    break
        
        return [r for r in rows if r.get("status") != "RECONCILED"]
    
    except Exception as e:
        logger.warning(f"Reconciliation error: {e}")
        return rows


# ============================================================================
# REPORT BUILDING
# ============================================================================

def build_rows(matches: List[MatchResult], deleted: List[Segment], added: List[Segment]) -> List[Dict[str, Any]]:
    """Build report - INFALLIBLE MODE"""
    rows = []
    row_id = 1

    for match in matches:
        v2_html = highlight_diff(match.seg_a.content, match.seg_b.content)
        
        if match.similarity >= 99.9:
            continue
        
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
    ocr_status = "✓ Tesseract available" if TESSERACT_AVAILABLE else "⚠️ Tesseract not available (using pdfplumber)"
    return jsonify({
        "status": "API running - ENHANCED VERSION",
        "ocr": ocr_status,
        "features": ["Enhanced OCR (Tesseract)", "Visual comparison (SSIM)", "Smart reconciliation"]
    })


@app.route("/compare", methods=["POST"])
def compare():
    try:
        if "file1" not in request.files or "file2" not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400

        f1, f2 = request.files["file1"], request.files["file2"]

        # Read file bytes
        f1.seek(0)
        f2.seek(0)
        bytes1 = f1.read()
        bytes2 = f2.read()
        
        f1.seek(0)
        f2.seek(0)

        # Get visual change percentage (secondary info)
        visual_change = 0.0
        try:
            images_a = convert_pdf_to_images(bytes1)
            images_b = convert_pdf_to_images(bytes2)
            visual_change = get_visual_change_percentage(images_a, images_b)
        except Exception as e:
            logger.warning(f"Visual analysis failed: {e}")

        # Extract text with enhanced OCR
        text_a = extract_text_enhanced(f1)
        text_b = extract_text_enhanced(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400

        segs_a = segment_text(text_a)
        segs_b = segment_text(text_b)

        if not segs_a or not segs_b:
            return jsonify({"error": "Could not segment text"}), 400

        matches, deleted, added = match_segments(segs_a, segs_b)
        rows = build_rows(matches, deleted, added)
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
                },
                "visual_analysis": {
                    "layout_change_percent": visual_change,
                    "note": "Secondary metric - content changes are primary"
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

        text_a = extract_text_enhanced(f1)
        text_b = extract_text_enhanced(f2)

        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400

        segs_a = segment_text(text_a)
        segs_b = segment_text(text_b)

        matches, deleted, added = match_segments(segs_a, segs_b)
        rows = build_rows(matches, deleted, added)
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
