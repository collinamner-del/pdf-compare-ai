"""
LAYOUT-AWARE PDF COMPARISON WITH INTELLIGENT BLOCK PAIRING

Solves the matching problem by:
1. Using word coordinates to group text by position
2. Creating semantic blocks (not just line breaks)
3. Matching blocks by position first, then content
4. Showing every difference including minor ones
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import re
from typing import List, Dict, Tuple, Optional
from difflib import SequenceMatcher
from collections import defaultdict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# ============================================================================
# LAYOUT-AWARE TEXT EXTRACTION
# ============================================================================

class LayoutAwareExtractor:
    """Extracts text using word coordinates for better structure preservation"""
    
    def __init__(self, page):
        self.page = page
    
    def extract_structured_text(self) -> str:
        """
        Extract text using word coordinates to preserve structure.
        Groups words by Y position (lines) then recreates natural blocks.
        """
        
        words = self.page.extract_words()
        
        if not words or len(words) < 5:
            # Fallback to simple extraction
            return self.page.extract_text() or ""
        
        # Group words by Y position (lines)
        lines = self._group_by_y_position(words)
        
        # Convert lines to text
        text_lines = []
        for line_words in lines:
            # Sort by X position (left to right)
            line_words.sort(key=lambda w: w['x0'])
            line_text = ' '.join([w['text'] for w in line_words])
            if line_text.strip():
                text_lines.append(line_text)
        
        # Join with newlines to preserve structure
        return '\n'.join(text_lines)
    
    def _group_by_y_position(self, words, tolerance=5):
        """Group words that are on approximately the same Y position"""
        lines = defaultdict(list)
        
        for word in words:
            # Round Y position to nearest tolerance
            y_key = round(word['top'] / tolerance) * tolerance
            lines[y_key].append(word)
        
        # Return lines sorted by Y position
        return [lines[y] for y in sorted(lines.keys())]

# ============================================================================
# INTELLIGENT BLOCK CREATION
# ============================================================================

def create_semantic_blocks(text: str) -> List[Dict]:
    """
    Create semantic blocks with position information.
    
    Instead of simple string blocks, returns blocks with metadata
    to aid matching.
    """
    
    blocks = []
    current_block = []
    current_y = None
    
    lines = text.split('\n')
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        if not line:
            # Blank line = block boundary
            if current_block:
                block_text = '\n'.join(current_block)
                if len(block_text) > 10:  # Only keep substantial blocks
                    blocks.append({
                        'text': block_text,
                        'line_start': i - len(current_block),
                        'line_end': i,
                        'line_count': len(current_block),
                        'char_count': len(block_text)
                    })
                current_block = []
                current_y = None
        else:
            current_block.append(line)
    
    # Don't forget last block
    if current_block:
        block_text = '\n'.join(current_block)
        if len(block_text) > 10:
            blocks.append({
                'text': block_text,
                'line_start': len(lines) - len(current_block),
                'line_end': len(lines),
                'line_count': len(current_block),
                'char_count': len(block_text)
            })
    
    return blocks

# ============================================================================
# INTELLIGENT BLOCK MATCHING WITH POSITION HINTS
# ============================================================================

class SmartBlockMatcher:
    """Matches blocks using position + content similarity"""
    
    def __init__(self, blocks_a: List[Dict], blocks_b: List[Dict]):
        self.blocks_a = blocks_a
        self.blocks_b = blocks_b
    
    def match_blocks(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Match blocks intelligently:
        1. Start with blocks at similar positions (position hint)
        2. Then verify with content similarity
        3. Handle deletions/additions
        
        Returns: (matched_pairs, deleted, added)
        """
        
        matched_pairs = []
        matched_b_indices = set()
        
        # Try to match each block in A
        for idx_a, block_a in enumerate(self.blocks_a):
            best_match_idx = None
            best_score = 0
            best_diff = None
            
            # Calculate position hint (normalized)
            pos_hint_a = block_a['line_start'] / max(1, len(self.blocks_a))
            
            for idx_b, block_b in enumerate(self.blocks_b):
                # Skip if already matched
                if idx_b in matched_b_indices:
                    continue
                
                # Position hint - blocks at similar positions more likely to match
                pos_hint_b = block_b['line_start'] / max(1, len(self.blocks_b))
                position_similarity = 100 - (abs(pos_hint_a - pos_hint_b) * 100)
                
                # Content similarity
                content_similarity = self._calculate_similarity(
                    block_a['text'], 
                    block_b['text']
                )
                
                # Combined score: 70% content, 30% position
                combined_score = (content_similarity * 0.7) + (position_similarity * 0.3)
                
                if combined_score > best_score:
                    best_score = combined_score
                    best_match_idx = idx_b
                    best_diff = self._create_detailed_diff(
                        block_a['text'], 
                        block_b['text']
                    )
            
            # If good match found, record it
            if best_match_idx is not None and best_score >= 60:  # Lower threshold
                matched_pairs.append({
                    'idx_a': idx_a,
                    'idx_b': best_match_idx,
                    'block_a': block_a,
                    'block_b': self.blocks_b[best_match_idx],
                    'score': best_score,
                    'diff': best_diff
                })
                matched_b_indices.add(best_match_idx)
        
        # Unmatched blocks
        matched_a_indices = {m['idx_a'] for m in matched_pairs}
        deleted = [
            block for i, block in enumerate(self.blocks_a) 
            if i not in matched_a_indices
        ]
        
        added = [
            block for i, block in enumerate(self.blocks_b) 
            if i not in matched_b_indices
        ]
        
        return matched_pairs, deleted, added
    
    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity 0-100"""
        if not text_a or not text_b:
            return 0.0
        
        # Normalize
        a_norm = ' '.join(text_a.split())
        b_norm = ' '.join(text_b.split())
        
        if a_norm == b_norm:
            return 100.0
        
        ratio = SequenceMatcher(None, a_norm, b_norm).ratio()
        return ratio * 100
    
    def _create_detailed_diff(self, text_a: str, text_b: str) -> Dict:
        """Create character-level diff showing every change"""
        
        if text_a == text_b:
            return {
                'status': 'NO CHANGE',
                'similarity': 100.0,
                'diff_html': text_a,
                'changes': []
            }
        
        # Normalize for comparison
        a_norm = ' '.join(text_a.split())
        b_norm = ' '.join(text_b.split())
        
        similarity = (SequenceMatcher(None, a_norm, b_norm).ratio()) * 100
        
        # Build character-level diff
        diff_html = self._build_char_diff(text_a, text_b)
        
        # Extract specific changes
        changes = self._extract_changes(text_a, text_b)
        
        # Determine status
        if similarity >= 98:
            status = 'NO CHANGE'
        elif similarity >= 90:
            status = 'MINOR_CHANGE'
        else:
            status = 'MODIFIED'
        
        return {
            'status': status,
            'similarity': round(similarity, 1),
            'diff_html': diff_html,
            'changes': changes
        }
    
    def _build_char_diff(self, text_a: str, text_b: str) -> str:
        """Build character-level diff with markup"""
        
        matcher = SequenceMatcher(None, text_a, text_b)
        result = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                result.append(text_b[j1:j2])
            elif tag == 'delete':
                # Deleted - show strikethrough
                deleted = text_a[i1:i2]
                result.append(f'~~{deleted}~~')
            elif tag == 'insert':
                # Added - show bold
                added = text_b[j1:j2]
                result.append(f'**{added}**')
            elif tag == 'replace':
                # Changed - show bold
                changed = text_b[j1:j2]
                result.append(f'**{changed}**')
        
        return ''.join(result)
    
    def _extract_changes(self, text_a: str, text_b: str) -> List[Dict]:
        """Extract specific changes from diff"""
        changes = []
        
        matcher = SequenceMatcher(None, text_a, text_b)
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                changes.append({
                    'type': 'MODIFIED',
                    'before': text_a[i1:i2],
                    'after': text_b[j1:j2]
                })
            elif tag == 'delete':
                changes.append({
                    'type': 'DELETED',
                    'before': text_a[i1:i2],
                    'after': ''
                })
            elif tag == 'insert':
                changes.append({
                    'type': 'ADDED',
                    'before': '',
                    'after': text_b[j1:j2]
                })
        
        return changes

# ============================================================================
# TEXT EXTRACTION
# ============================================================================

def extract_text_with_layout(file) -> str:
    """Extract text preserving layout structure"""
    try:
        if hasattr(file, 'seek'):
            file.seek(0)
        
        with pdfplumber.open(file) as pdf:
            all_text = ""
            
            for page in pdf.pages:
                try:
                    # Try layout-aware extraction
                    extractor = LayoutAwareExtractor(page)
                    page_text = extractor.extract_structured_text()
                    all_text += page_text + "\n\n"
                except Exception as e:
                    # Fallback to simple extraction
                    print(f"Layout extraction failed: {str(e)}")
                    text = page.extract_text()
                    if text:
                        all_text += text + "\n\n"
            
            return all_text
    
    except Exception as e:
        raise Exception(f"Failed to extract text: {str(e)}")

# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_comparison_report(blocks_a: List[Dict], blocks_b: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Generate comprehensive comparison report"""
    
    matcher = SmartBlockMatcher(blocks_a, blocks_b)
    matched_pairs, deleted_blocks, added_blocks = matcher.match_blocks()
    
    report_rows = []
    row_id = 1
    
    # Sort matched pairs by position
    matched_pairs.sort(key=lambda m: m['idx_a'])
    
    # ===== MATCHED PAIRS =====
    for match in matched_pairs:
        diff = match['diff']
        
        # Show all changes, even minor ones
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {match['idx_a'] + 1}",
            "pdf_a_content": match['block_a']['text'][:150],
            "pdf_b_content": diff['diff_html'][:150],
            "status": diff['status'],
            "comments": f"{diff['similarity']:.0f}% match | {len(diff['changes'])} change(s)",
            "full_diff": diff
        })
        row_id += 1
    
    # ===== DELETED BLOCKS =====
    for deleted in deleted_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {deleted['line_start']}",
            "pdf_a_content": deleted['text'][:150],
            "pdf_b_content": "❌ [DELETED]",
            "status": "DELETED",
            "comments": "Entire block removed"
        })
        row_id += 1
    
    # ===== ADDED BLOCKS =====
    for added in added_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block New",
            "pdf_a_content": "",
            "pdf_b_content": f"✅ {added['text'][:150]}",
            "status": "ADDED",
            "comments": "New block added"
        })
        row_id += 1
    
    summary = {
        "total_rows": len(report_rows),
        "no_change": sum(1 for r in report_rows if r['status'] == 'NO CHANGE'),
        "minor_change": sum(1 for r in report_rows if r['status'] == 'MINOR_CHANGE'),
        "modified": sum(1 for r in report_rows if r['status'] == 'MODIFIED'),
        "added": sum(1 for r in report_rows if r['status'] == 'ADDED'),
        "deleted": sum(1 for r in report_rows if r['status'] == 'DELETED'),
        "matched_pairs": len(matched_pairs)
    }
    
    return report_rows, summary

# ============================================================================
# FLASK ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - Layout-Aware Comparison"})

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
        
        # Extract with layout awareness
        text_a = extract_text_with_layout(f1)
        text_b = extract_text_with_layout(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        # Create semantic blocks
        blocks_a = create_semantic_blocks(text_a)
        blocks_b = create_semantic_blocks(text_b)
        
        if not blocks_a or not blocks_b:
            return jsonify({"error": "Could not create blocks"}), 400
        
        # Generate report
        report_rows, summary = generate_comparison_report(blocks_a, blocks_b)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Layout-aware block matching with character-level diff",
                "comparison_table": report_rows,
                "summary": {
                    "total_rows": summary['total_rows'],
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
        
        text_a = extract_text_with_layout(f1)
        text_b = extract_text_with_layout(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        blocks_a = create_semantic_blocks(text_a)
        blocks_b = create_semantic_blocks(text_b)
        
        report_rows, summary = generate_comparison_report(blocks_a, blocks_b)
        
        # Build detailed summary
        real_changes = [r for r in report_rows if r['status'] in ['MODIFIED', 'ADDED', 'DELETED', 'MINOR_CHANGE']]
        
        changes_text = ""
        for i, change in enumerate(real_changes[:50], 1):
            changes_text += f"{i}. [{change['status']}] {change['comments']}\n"
            changes_text += f"   PDF 1: {change['pdf_a_content'][:80]}\n"
            changes_text += f"   PDF 2: {change['pdf_b_content'][:80]}\n\n"
        
        if not changes_text:
            changes_text = "PDFs are identical - no changes detected"
        
        qc_prompt = f"""Professional QC checklist from PDF comparison (every difference shown).

COMPARISON RESULTS:
{changes_text}

SUMMARY:
- Total items: {summary['total_rows']}
- Identical: {summary['no_change']}
- Minor changes: {summary['minor_change']}
- Modified: {summary['modified']}
- Added: {summary['added']}
- Deleted: {summary['deleted']}

Create a professional QC checklist with:
1. Checkbox format [ ]
2. Clear item numbers
3. Before/after comparison
4. Action required
5. Summary section

Make it easy for printing and manual verification."""

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
