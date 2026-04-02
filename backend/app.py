"""
FUZZY MATCHING PDF COMPARISON

Instead of flagging every difference, this scores similarity:
- 95%+ match = Same block (NO CHANGE)
- 85-95% match = Minor variation (SKIP or note)
- <85% match = Real change (MODIFIED)

This dramatically reduces false positives!
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
import re
from typing import List, Dict, Tuple
from collections import defaultdict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# ============================================================================
# SIMILARITY SCORING
# ============================================================================

def calculate_similarity(text_a: str, text_b: str) -> float:
    """
    Calculate similarity between two text blocks as percentage (0-100).
    
    Uses SequenceMatcher ratio which is fast and effective.
    """
    if not text_a or not text_b:
        return 0.0
    
    # Normalize text (remove extra spaces)
    text_a_norm = ' '.join(text_a.split())
    text_b_norm = ' '.join(text_b.split())
    
    # Calculate similarity ratio (0.0 to 1.0)
    ratio = difflib.SequenceMatcher(None, text_a_norm, text_b_norm).ratio()
    
    return ratio * 100  # Return as percentage

def categorize_change(similarity_score: float, threshold_high=95, threshold_low=85) -> str:
    """
    Categorize change based on similarity score.
    
    95%+  = Essentially same (minor typo/spacing)
    85-95% = Similar but changed
    <85%  = Real change
    """
    if similarity_score >= threshold_high:
        return "SAME"
    elif similarity_score >= threshold_low:
        return "SIMILAR"
    else:
        return "CHANGED"

class FuzzyComparator:
    """Compare blocks with fuzzy matching"""
    
    def __init__(self, threshold_show_change=85):
        """
        threshold_show_change: Only show changes below this similarity %
        
        Typical values:
        - 90: Only show real changes (strict)
        - 85: Show meaningful changes (recommended)
        - 80: Show all changes (permissive)
        """
        self.threshold = threshold_show_change
    
    def compare(self, blocks_a: List[str], blocks_b: List[str]) -> Tuple[List[Dict], Dict]:
        """
        Compare blocks using fuzzy matching.
        Returns: (comparison_rows, statistics)
        """
        
        rows = []
        stats = {
            'total': 0,
            'identical': 0,
            'minor_variation': 0,
            'modified': 0,
            'added': 0,
            'deleted': 0,
            'similarity_scores': []
        }
        
        # Use SequenceMatcher for best-matching blocks
        matcher = difflib.SequenceMatcher(None, blocks_a, blocks_b)
        row_id = 1
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            
            if tag == 'equal':
                # Identical blocks
                for i in range(i2 - i1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": blocks_a[i1 + i][:150],
                        "pdf_b_content": blocks_a[i1 + i][:150],
                        "similarity": 100.0,
                        "status": "NO CHANGE",
                        "comments": "Identical"
                    })
                    stats['identical'] += 1
                    stats['total'] += 1
                    row_id += 1
            
            elif tag == 'replace':
                # Blocks changed - but how much?
                max_blocks = max(i2 - i1, j2 - j1)
                
                for i in range(max_blocks):
                    a_block = blocks_a[i1 + i] if i1 + i < i2 else ""
                    b_block = blocks_b[j1 + i] if j1 + i < j2 else ""
                    
                    if a_block and b_block:
                        # Both exist - calculate similarity
                        similarity = calculate_similarity(a_block, b_block)
                        category = categorize_change(similarity, self.threshold, self.threshold - 10)
                        
                        stats['similarity_scores'].append(similarity)
                        
                        if category == "SAME":
                            # Very similar - probably same block
                            rows.append({
                                "row_id": f"R{row_id}",
                                "tag": f"Block {i1 + i + 1}",
                                "pdf_a_content": a_block[:150],
                                "pdf_b_content": b_block[:150],
                                "similarity": round(similarity, 1),
                                "status": "NO CHANGE",
                                "comments": f"Essentially same ({similarity:.0f}% match)"
                            })
                            stats['identical'] += 1
                        
                        elif category == "SIMILAR":
                            # Similar but with changes
                            b_bold = self._highlight_differences(a_block, b_block)
                            rows.append({
                                "row_id": f"R{row_id}",
                                "tag": f"Block {i1 + i + 1}",
                                "pdf_a_content": a_block[:150],
                                "pdf_b_content": b_bold[:150],
                                "similarity": round(similarity, 1),
                                "status": "MINOR_CHANGE",
                                "comments": f"Minor variations ({similarity:.0f}% match)"
                            })
                            stats['minor_variation'] += 1
                        
                        else:
                            # Real change
                            b_bold = self._highlight_differences(a_block, b_block)
                            rows.append({
                                "row_id": f"R{row_id}",
                                "tag": f"Block {i1 + i + 1}",
                                "pdf_a_content": a_block[:150],
                                "pdf_b_content": b_bold[:150],
                                "similarity": round(similarity, 1),
                                "status": "MODIFIED",
                                "comments": f"Significant change ({similarity:.0f}% match)"
                            })
                            stats['modified'] += 1
                        
                        stats['total'] += 1
                        row_id += 1
                    
                    elif a_block and not b_block:
                        # Deleted
                        rows.append({
                            "row_id": f"R{row_id}",
                            "tag": f"Block {i1 + i + 1}",
                            "pdf_a_content": a_block[:150],
                            "pdf_b_content": "❌ [DELETED]",
                            "similarity": 0.0,
                            "status": "DELETED",
                            "comments": "Content removed"
                        })
                        stats['deleted'] += 1
                        stats['total'] += 1
                        row_id += 1
                    
                    elif not a_block and b_block:
                        # Added
                        rows.append({
                            "row_id": f"R{row_id}",
                            "tag": f"Block New",
                            "pdf_a_content": "",
                            "pdf_b_content": f"✅ **{b_block[:150]}**",
                            "similarity": 0.0,
                            "status": "ADDED",
                            "comments": "New content"
                        })
                        stats['added'] += 1
                        stats['total'] += 1
                        row_id += 1
            
            elif tag == 'delete':
                # Blocks deleted
                for i in range(i2 - i1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": blocks_a[i1 + i][:150],
                        "pdf_b_content": "❌ [DELETED]",
                        "similarity": 0.0,
                        "status": "DELETED",
                        "comments": "Content removed"
                    })
                    stats['deleted'] += 1
                    stats['total'] += 1
                    row_id += 1
            
            elif tag == 'insert':
                # Blocks added
                for i in range(j2 - j1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block New",
                        "pdf_a_content": "",
                        "pdf_b_content": f"✅ **{blocks_b[j1 + i][:150]}**",
                        "similarity": 0.0,
                        "status": "ADDED",
                        "comments": "New content"
                    })
                    stats['added'] += 1
                    stats['total'] += 1
                    row_id += 1
        
        return rows, stats
    
    def _highlight_differences(self, text_a: str, text_b: str) -> str:
        """Highlight changed parts"""
        if not text_a or not text_b:
            return f"**{text_b}**"
        
        matcher = difflib.SequenceMatcher(None, text_a, text_b)
        result = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                result.append(text_b[j1:j2])
            elif tag in ['replace', 'insert']:
                result.append(f"**{text_b[j1:j2]}**")
        
        return ''.join(result)

# ============================================================================
# TEXT EXTRACTION (simplified - use from previous version)
# ============================================================================

def extract_text(file) -> str:
    """Extract text from PDF"""
    try:
        if hasattr(file, 'seek'):
            file.seek(0)
        
        with pdfplumber.open(file) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except Exception as e:
        raise Exception(f"Failed to extract text: {str(e)}")

def split_into_blocks(text):
    """Split text into blocks (sentences/paragraphs)"""
    blocks = []
    
    # Split by paragraph breaks first
    sections = text.split('\n\n')
    
    for section in sections:
        if not section.strip():
            continue
        
        # Split by sentence endings
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', section.strip())
        
        for sent in sentences:
            sent = sent.strip()
            if sent:
                blocks.append(sent)
    
    return blocks

# ============================================================================
# FLASK ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - Fuzzy Match Comparison"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        if not f1 or not f1.filename or not f2 or not f2.filename:
            return jsonify({"error": "Files missing"}), 400
        
        if not f1.filename.lower().endswith('.pdf') or not f2.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Both files must be PDFs"}), 400
        
        # Extract text
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        # Split into blocks
        blocks_a = split_into_blocks(text_a)
        blocks_b = split_into_blocks(text_b)
        
        # Compare with fuzzy matching (threshold: 85%)
        comparator = FuzzyComparator(threshold_show_change=85)
        comparison_rows, stats = comparator.compare(blocks_a, blocks_b)
        
        # Filter out the similarity field for frontend compatibility
        # Keep only fields the frontend expects
        clean_rows = []
        for row in comparison_rows:
            clean_row = {
                "row_id": row.get("row_id"),
                "tag": row.get("tag"),
                "pdf_a_content": row.get("pdf_a_content"),
                "pdf_b_content": row.get("pdf_b_content"),
                "status": row.get("status"),
                "comments": row.get("comments")
            }
            clean_rows.append(clean_row)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Fuzzy match comparison - only shows real changes",
                "comparison_table": clean_rows,
                "summary": {
                    "total_rows": stats['total'],
                    "no_change": stats['identical'],
                    "modified": stats['modified'],
                    "added": stats['added'],
                    "deleted": stats['deleted']
                }
            }
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/summary", methods=["POST"])
def summary():
    try:
        if not OPENAI_API_KEY:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        if not f1 or not f1.filename or not f2 or not f2.filename:
            return jsonify({"error": "Files missing"}), 400
        
        if not f1.filename.lower().endswith('.pdf') or not f2.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Both files must be PDFs"}), 400
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        blocks_a = split_into_blocks(text_a)
        blocks_b = split_into_blocks(text_b)
        
        # Fuzzy comparison
        comparator = FuzzyComparator(threshold_show_change=85)
        comparison_rows, stats = comparator.compare(blocks_a, blocks_b)
        
        # Only include real changes (skip MINOR_CHANGE and NO CHANGE)
        important_changes = [r for r in comparison_rows 
                           if r.get('status') in ['MODIFIED', 'ADDED', 'DELETED']]
        
        changes_detail = []
        for i, change in enumerate(important_changes[:30], 1):
            status = change.get('status', '')
            pdf1 = change.get('pdf_a_content', '')
            pdf2 = change.get('pdf_b_content', '').replace('**', '').replace('❌', '').replace('✅', '').strip()
            
            if status == 'DELETED':
                changes_detail.append(f"{i}. [ ] PDF 1: \"{pdf1}\"   PDF 2: ❌ [DELETED]   ACTION: Verify")
            elif status == 'ADDED':
                changes_detail.append(f"{i}. [ ] PDF 1: [NEW]   PDF 2: \"{pdf2}\"   ACTION: Verify")
            elif status == 'MODIFIED':
                changes_detail.append(f"{i}. [ ] PDF 1: \"{pdf1}\"   PDF 2: \"{pdf2}\"   ACTION: Verify")
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No real changes detected"
        
        qc_prompt = f"""Professional QC checklist of actual changes between PDFs.

CHANGES FOUND:
{changes_text if changes_detail else "No real changes detected"}

Format response as a checkbox list:
[ ] PDF 1: "old text"   PDF 2: "new text"   ACTION: Verify

Keep professional and specific. Only include actual changes."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": qc_prompt}],
            "temperature": 0.3,
            "max_tokens": 2000
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({"error": f"OpenAI error: {response.text}"}), 500
        
        result = response.json()
        summary_text = result["choices"][0]["message"]["content"]
        
        return jsonify({"summary": summary_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
