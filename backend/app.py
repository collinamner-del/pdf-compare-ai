"""
INTELLIGENT PDF COMPARISON WITH BLOCK MATCHING

Algorithm:
1. Extract text blocks from both PDFs
2. For EACH block in PDF 1, find the BEST matching block in PDF 2
3. Calculate similarity score for each match
4. Identify unmatched blocks (deleted/added)
5. Generate clear QC report
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import re
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# ============================================================================
# INTELLIGENT BLOCK MATCHING
# ============================================================================

class BlockMatcher:
    """Intelligently matches text blocks across two PDFs"""
    
    def __init__(self, blocks_a: List[str], blocks_b: List[str]):
        self.blocks_a = blocks_a
        self.blocks_b = blocks_b
        self.matches = []
        self.unmatched_a = set(range(len(blocks_a)))
        self.unmatched_b = set(range(len(blocks_b)))
    
    def match_blocks(self, similarity_threshold=80) -> List[Dict]:
        """
        Match blocks across PDFs intelligently.
        
        For each block in A, find best match in B.
        Returns list of matched pairs with similarity scores.
        """
        
        matches = []
        
        # For each block in PDF A, find best match in PDF B
        for idx_a, block_a in enumerate(self.blocks_a):
            best_match_idx = None
            best_similarity = 0
            
            # Score this block against all blocks in B
            for idx_b, block_b in enumerate(self.blocks_b):
                # Skip if already matched
                if idx_b not in self.unmatched_b:
                    continue
                
                # Calculate similarity
                similarity = self._calculate_similarity(block_a, block_b)
                
                # Keep track of best match
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match_idx = idx_b
            
            # If we found a match above threshold, record it
            if best_match_idx is not None and best_similarity >= similarity_threshold:
                matches.append({
                    'idx_a': idx_a,
                    'idx_b': best_match_idx,
                    'block_a': block_a,
                    'block_b': self.blocks_b[best_match_idx],
                    'similarity': best_similarity
                })
                
                # Mark as matched
                self.unmatched_a.discard(idx_a)
                self.unmatched_b.discard(best_match_idx)
            
            else:
                # No good match found
                self.unmatched_a.discard(idx_a)
        
        return matches
    
    def get_unmatched_a(self) -> List[Dict]:
        """Get blocks from PDF A that weren't matched (deleted)"""
        unmatched = []
        for idx in self.unmatched_a:
            unmatched.append({
                'idx': idx,
                'block': self.blocks_a[idx],
                'type': 'DELETED'
            })
        return unmatched
    
    def get_unmatched_b(self) -> List[Dict]:
        """Get blocks from PDF B that weren't matched (added)"""
        unmatched = []
        for idx in self.unmatched_b:
            unmatched.append({
                'idx': idx,
                'block': self.blocks_b[idx],
                'type': 'ADDED'
            })
        return unmatched
    
    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """
        Calculate similarity between two blocks (0-100).
        
        Uses multiple methods:
        1. Exact character matching (SequenceMatcher)
        2. Normalized comparison (spaces removed)
        3. Partial matching (if one is substring of other)
        """
        
        if not text_a or not text_b:
            return 0.0
        
        # Method 1: Direct character comparison
        import difflib
        ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
        similarity_direct = ratio * 100
        
        # Method 2: Normalized comparison (remove extra spaces)
        text_a_norm = ' '.join(text_a.split())
        text_b_norm = ' '.join(text_b.split())
        
        if text_a_norm == text_b_norm:
            return 100.0  # Exact match after normalization
        
        ratio_norm = difflib.SequenceMatcher(None, text_a_norm, text_b_norm).ratio()
        similarity_norm = ratio_norm * 100
        
        # Method 3: Check if one contains the other (for partial matches)
        min_len = min(len(text_a_norm), len(text_b_norm))
        max_len = max(len(text_a_norm), len(text_b_norm))
        
        if text_a_norm in text_b_norm or text_b_norm in text_a_norm:
            # Substring match - adjust score based on length ratio
            similarity_partial = (min_len / max_len) * 100
        else:
            similarity_partial = 0
        
        # Return best score from all methods
        return max(similarity_direct, similarity_norm, similarity_partial)

# ============================================================================
# TEXT EXTRACTION
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

def split_into_blocks(text) -> List[str]:
    """
    Split text into meaningful blocks.
    
    Blocks are:
    - Paragraphs (separated by blank lines)
    - Sentences (ending with . ! ?)
    - But preserves natural grouping
    """
    blocks = []
    
    # First, split by paragraph (double newlines)
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if not para.strip():
            continue
        
        # Clean up the paragraph
        para = para.strip()
        
        # Split by sentence ending, but keep sentences together
        # Match . ! ? followed by space and uppercase letter
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para)
        
        for sent in sentences:
            sent = sent.strip()
            # Only keep substantial blocks (>10 chars)
            if len(sent) > 10:
                blocks.append(sent)
            # For very short blocks, try to group them
            elif sent and blocks and len(blocks[-1]) < 100:
                blocks[-1] += ' ' + sent
    
    return blocks

# ============================================================================
# COMPARISON AND REPORTING
# ============================================================================

def generate_comparison_report(blocks_a: List[str], blocks_b: List[str]) -> Tuple[List[Dict], Dict]:
    """
    Generate full comparison report with intelligent block matching.
    
    Returns: (report_rows, summary_stats)
    """
    
    # Match blocks
    matcher = BlockMatcher(blocks_a, blocks_b)
    matched_pairs = matcher.match_blocks(similarity_threshold=75)
    deleted_blocks = matcher.get_unmatched_a()
    added_blocks = matcher.get_unmatched_b()
    
    report_rows = []
    row_id = 1
    
    # ===== MATCHED BLOCKS =====
    for match in matched_pairs:
        idx_a = match['idx_a']
        block_a = match['block_a']
        block_b = match['block_b']
        similarity = match['similarity']
        
        # Determine status based on similarity
        if similarity >= 98:
            status = "NO CHANGE"
            comments = f"Identical ({similarity:.0f}% match)"
        elif similarity >= 90:
            status = "NO CHANGE"
            comments = f"Essentially same ({similarity:.0f}% match)"
        elif similarity >= 85:
            status = "MINOR_CHANGE"
            comments = f"Minor variation ({similarity:.0f}% match)"
        else:
            status = "MODIFIED"
            comments = f"Changed ({similarity:.0f}% match)"
        
        # Highlight differences
        if similarity < 98:
            block_b_display = highlight_differences(block_a, block_b)
        else:
            block_b_display = block_b
        
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {idx_a + 1}",
            "pdf_a_content": block_a[:120],
            "pdf_b_content": block_b_display[:120],
            "status": status,
            "comments": comments
        })
        row_id += 1
    
    # ===== DELETED BLOCKS (in A but not matched in B) =====
    for deleted in deleted_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {deleted['idx'] + 1}",
            "pdf_a_content": deleted['block'][:120],
            "pdf_b_content": "❌ [DELETED]",
            "status": "DELETED",
            "comments": "Content removed from PDF 2"
        })
        row_id += 1
    
    # ===== ADDED BLOCKS (in B but not matched in A) =====
    for added in added_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block New",
            "pdf_a_content": "",
            "pdf_b_content": f"✅ **{added['block'][:120]}**",
            "status": "ADDED",
            "comments": "New content in PDF 2"
        })
        row_id += 1
    
    # Generate summary stats
    summary = {
        "total_blocks": len(report_rows),
        "no_change": sum(1 for r in report_rows if r['status'] == 'NO CHANGE'),
        "minor_change": sum(1 for r in report_rows if r['status'] == 'MINOR_CHANGE'),
        "modified": sum(1 for r in report_rows if r['status'] == 'MODIFIED'),
        "added": sum(1 for r in report_rows if r['status'] == 'ADDED'),
        "deleted": sum(1 for r in report_rows if r['status'] == 'DELETED'),
        "matched_blocks": len(matched_pairs),
        "unmatched_from_a": len(deleted_blocks),
        "unmatched_from_b": len(added_blocks)
    }
    
    return report_rows, summary

def highlight_differences(text_a: str, text_b: str) -> str:
    """Highlight only the changed parts with bold"""
    import difflib
    
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
# FLASK ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - Intelligent Block Matching"})

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
        
        if not blocks_a or not blocks_b:
            return jsonify({"error": "Could not extract blocks from PDFs"}), 400
        
        # Generate full comparison report
        report_rows, summary = generate_comparison_report(blocks_a, blocks_b)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Intelligent block matching comparison",
                "comparison_table": report_rows,
                "summary": {
                    "total_rows": summary['total_blocks'],
                    "no_change": summary['no_change'],
                    "modified": summary['modified'],
                    "added": summary['added'],
                    "deleted": summary['deleted']
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
        
        report_rows, summary = generate_comparison_report(blocks_a, blocks_b)
        
        # Build changes list for AI
        real_changes = [r for r in report_rows 
                       if r['status'] in ['MODIFIED', 'ADDED', 'DELETED']]
        
        changes_detail = []
        for i, change in enumerate(real_changes[:40], 1):
            status = change.get('status', '')
            pdf1 = change.get('pdf_a_content', '')
            pdf2 = change.get('pdf_b_content', '').replace('**', '').replace('❌', '').replace('✅', '').strip()
            comments = change.get('comments', '')
            
            if status == 'DELETED':
                changes_detail.append(f"{i}. [ ] PDF 1: \"{pdf1}\"")
                changes_detail.append(f"       PDF 2: ❌ [DELETED]")
                changes_detail.append(f"       ACTION: Verify removal")
            elif status == 'ADDED':
                changes_detail.append(f"{i}. [ ] PDF 1: [NEW]")
                changes_detail.append(f"       PDF 2: \"{pdf2}\"")
                changes_detail.append(f"       ACTION: Verify addition")
            elif status == 'MODIFIED':
                changes_detail.append(f"{i}. [ ] PDF 1: \"{pdf1}\"")
                changes_detail.append(f"       PDF 2: \"{pdf2}\"")
                changes_detail.append(f"       {comments}")
                changes_detail.append(f"       ACTION: Verify change")
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No changes detected"
        
        qc_prompt = f"""Create a professional QC checklist from this PDF comparison.

DETECTED CHANGES (via intelligent block matching):
{changes_text}

SUMMARY:
- Total blocks: {summary['total_blocks']}
- No changes: {summary['no_change']}
- Modified: {summary['modified']}
- Added: {summary['added']}
- Deleted: {summary['deleted']}

Format as clear checkbox list:
[ ] Item 1: PDF 1 says "X" | PDF 2 says "Y" | ACTION: Verify

Make it easy for a QC person to print and check off each item.
Be specific and professional."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": qc_prompt}],
            "temperature": 0.3,
            "max_tokens": 2500
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
