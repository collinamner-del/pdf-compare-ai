"""
PREMIUM PDF COMPARISON WITH WORD-LEVEL DIFFING

Achieves perfect pack copy verification by:
1. Comparing blocks word-by-word (not just similarity score)
2. Detecting critical fields (allergens, weights, dates, etc.)
3. Showing EXACTLY what changed in clear format
4. Generating professional QC reports with proper formatting
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
# WORD-LEVEL COMPARISON ENGINE
# ============================================================================

class WordLevelComparator:
    """Compares blocks at word level for precise diff detection"""
    
    # Critical fields that MUST be flagged
    CRITICAL_PATTERNS = {
        'ALLERGEN': r'(allerg|contain|may contain|traces?|gluten|dairy|nuts|peanuts|sesame|soy)',
        'WEIGHT': r'(\d+\.?\d*\s*(?:g|kg|mg|ml|oz|lb))',
        'PERCENTAGE': r'(\d+\.?\d*\s*%)',
        'DATE': r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        'TEMPERATURE': r'(°?c|-\d+|freeze|fridge)',
        'BARCODE': r'(\d{12,14})',
    }
    
    def compare_blocks(self, block_a: str, block_b: str) -> Dict:
        """
        Compare two blocks word-by-word.
        
        Returns detailed diff with:
        - Word-level changes
        - Critical field flags
        - Change severity
        - Visual diff
        """
        
        if block_a == block_b:
            return {
                'status': 'NO CHANGE',
                'similarity': 100.0,
                'word_changes': 0,
                'critical_flags': [],
                'word_diff': block_a,
                'summary': 'Identical blocks'
            }
        
        # Split into words
        words_a = block_a.split()
        words_b = block_b.split()
        
        # Word-level diff
        matcher = SequenceMatcher(None, words_a, words_b)
        
        # Calculate stats
        similarity = matcher.ratio() * 100
        word_changes = sum(1 for op in matcher.get_opcodes() if op[0] != 'equal')
        
        # Detect critical field changes
        critical_flags = self._detect_critical_changes(block_a, block_b)
        
        # Build visual diff
        word_diff_html = self._build_word_diff(words_a, words_b)
        
        # Determine status
        if similarity >= 98:
            status = 'NO CHANGE'
        elif similarity >= 90:
            status = 'MINOR_CHANGE'
        elif similarity >= 85 and len(critical_flags) == 0:
            status = 'MINOR_CHANGE'
        else:
            status = 'MODIFIED'
        
        return {
            'status': status,
            'similarity': round(similarity, 1),
            'word_changes': word_changes,
            'critical_flags': critical_flags,
            'word_diff': word_diff_html,
            'block_a': block_a,
            'block_b': block_b,
            'summary': self._generate_summary(status, similarity, critical_flags, word_changes)
        }
    
    def _detect_critical_changes(self, block_a: str, block_b: str) -> List[str]:
        """Detect changes in critical fields"""
        flags = []
        
        for field_type, pattern in self.CRITICAL_PATTERNS.items():
            values_a = re.findall(pattern, block_a, re.IGNORECASE)
            values_b = re.findall(pattern, block_b, re.IGNORECASE)
            
            if values_a != values_b:
                # Critical field changed
                flags.append({
                    'type': field_type,
                    'before': values_a[0] if values_a else 'N/A',
                    'after': values_b[0] if values_b else 'N/A'
                })
        
        return flags
    
    def _build_word_diff(self, words_a: List[str], words_b: List[str]) -> str:
        """Build word-by-word diff visualization"""
        matcher = SequenceMatcher(None, words_a, words_b)
        
        result = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                result.extend(words_b[j1:j2])
            elif tag == 'delete':
                # Deleted words
                for word in words_a[i1:i2]:
                    result.append(f"~~{word}~~")
            elif tag == 'insert':
                # New words
                for word in words_b[j1:j2]:
                    result.append(f"**{word}**")
            elif tag == 'replace':
                # Changed words
                for word in words_b[j1:j2]:
                    result.append(f"**{word}**")
        
        return ' '.join(result)
    
    def _generate_summary(self, status: str, similarity: float, 
                         critical_flags: List, word_changes: int) -> str:
        """Generate human-readable summary"""
        
        if status == 'NO CHANGE':
            return 'Identical'
        
        summary_parts = [f"{similarity:.0f}% match"]
        
        if word_changes > 0:
            summary_parts.append(f"{word_changes} word(s) changed")
        
        if critical_flags:
            flag_types = ', '.join([f['type'] for f in critical_flags])
            summary_parts.append(f"⚠️ {flag_types} changed")
        
        return ' | '.join(summary_parts)

# ============================================================================
# TEXT EXTRACTION & BLOCKING
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
    Split text into meaningful blocks for QC comparison.
    
    Blocks are roughly paragraph-sized for accurate comparison.
    """
    blocks = []
    
    # First split by paragraph breaks
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if not para.strip():
            continue
        
        para = para.strip()
        
        # Split by sentence endings
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para)
        
        for sent in sentences:
            sent = sent.strip()
            if len(sent) > 8:  # Only keep substantial blocks
                blocks.append(sent)
    
    return blocks

# ============================================================================
# INTELLIGENT BLOCK MATCHING (Enhanced)
# ============================================================================

class EnhancedBlockMatcher:
    """Enhanced matcher with word-level comparison"""
    
    def __init__(self, blocks_a: List[str], blocks_b: List[str]):
        self.blocks_a = blocks_a
        self.blocks_b = blocks_b
        self.comparator = WordLevelComparator()
        self.matches = []
    
    def match_blocks(self, threshold=75) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Match blocks with word-level comparison.
        
        Returns: (matched_pairs, deleted_blocks, added_blocks)
        """
        matched_pairs = []
        matched_b = set()
        
        # For each block in A, find best match in B
        for idx_a, block_a in enumerate(self.blocks_a):
            best_match_idx = None
            best_score = 0
            best_comparison = None
            
            for idx_b, block_b in enumerate(self.blocks_b):
                if idx_b in matched_b:
                    continue
                
                # Compare blocks word-by-word
                comparison = self.comparator.compare_blocks(block_a, block_b)
                similarity = comparison['similarity']
                
                if similarity > best_score:
                    best_score = similarity
                    best_match_idx = idx_b
                    best_comparison = comparison
            
            # If good match found, record it
            if best_match_idx is not None and best_score >= threshold:
                matched_pairs.append({
                    'idx_a': idx_a,
                    'idx_b': best_match_idx,
                    'block_a': block_a,
                    'block_b': self.blocks_b[best_match_idx],
                    'comparison': best_comparison
                })
                matched_b.add(best_match_idx)
        
        # Get unmatched blocks
        matched_a = {m['idx_a'] for m in matched_pairs}
        deleted_blocks = [
            {'idx': i, 'block': self.blocks_a[i]}
            for i in range(len(self.blocks_a)) if i not in matched_a
        ]
        
        added_blocks = [
            {'idx': i, 'block': self.blocks_b[i]}
            for i in range(len(self.blocks_b)) if i not in matched_b
        ]
        
        return matched_pairs, deleted_blocks, added_blocks

# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_professional_report(blocks_a: List[str], blocks_b: List[str]) -> Tuple[List[Dict], Dict]:
    """Generate professional QC report"""
    
    matcher = EnhancedBlockMatcher(blocks_a, blocks_b)
    matched_pairs, deleted_blocks, added_blocks = matcher.match_blocks(threshold=70)
    
    report_rows = []
    row_id = 1
    
    # ===== MATCHED BLOCKS WITH WORD-LEVEL DIFF =====
    for match in matched_pairs:
        comparison = match['comparison']
        
        # Only show if there's actual change
        if comparison['status'] == 'NO CHANGE' and len(comparison['critical_flags']) == 0:
            # Skip identical blocks (too many in report)
            continue
        
        # Build detailed diff
        if comparison['critical_flags']:
            # Critical field changed - show with flags
            flag_details = []
            for flag in comparison['critical_flags']:
                flag_details.append(f"[{flag['type']}: {flag['before']} → {flag['after']}]")
            
            pdf_b_display = f"{comparison['block_b']}\n⚠️ {' '.join(flag_details)}"
        else:
            pdf_b_display = comparison['word_diff']
        
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {match['idx_a'] + 1}",
            "pdf_a_content": comparison['block_a'][:130],
            "pdf_b_content": pdf_b_display[:130],
            "status": comparison['status'],
            "comments": comparison['summary'],
            "is_critical": len(comparison['critical_flags']) > 0
        })
        row_id += 1
    
    # ===== DELETED BLOCKS =====
    for deleted in deleted_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block {deleted['idx'] + 1}",
            "pdf_a_content": deleted['block'][:130],
            "pdf_b_content": "❌ [DELETED]",
            "status": "DELETED",
            "comments": "This block was removed",
            "is_critical": True
        })
        row_id += 1
    
    # ===== ADDED BLOCKS =====
    for added in added_blocks:
        report_rows.append({
            "row_id": f"R{row_id}",
            "tag": f"Block New",
            "pdf_a_content": "",
            "pdf_b_content": f"✅ {added['block'][:130]}",
            "status": "ADDED",
            "comments": "This block was added",
            "is_critical": True
        })
        row_id += 1
    
    # Summary stats
    summary = {
        "total_blocks": len(report_rows),
        "no_change": sum(1 for r in report_rows if r['status'] == 'NO CHANGE'),
        "minor_change": sum(1 for r in report_rows if r['status'] == 'MINOR_CHANGE'),
        "modified": sum(1 for r in report_rows if r['status'] == 'MODIFIED'),
        "added": sum(1 for r in report_rows if r['status'] == 'ADDED'),
        "deleted": sum(1 for r in report_rows if r['status'] == 'DELETED'),
        "critical_items": sum(1 for r in report_rows if r.get('is_critical', False))
    }
    
    return report_rows, summary

# ============================================================================
# FLASK ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - Premium Word-Level Comparison"})

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
        
        # Extract and process
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        blocks_a = split_into_blocks(text_a)
        blocks_b = split_into_blocks(text_b)
        
        if not blocks_a or not blocks_b:
            return jsonify({"error": "Could not create comparison blocks"}), 400
        
        # Generate professional report
        report_rows, summary = generate_professional_report(blocks_a, blocks_b)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Word-level pack copy comparison",
                "comparison_table": report_rows,
                "summary": {
                    "total_rows": summary['total_blocks'],
                    "no_change": summary['no_change'],
                    "modified": summary['modified'],
                    "added": summary['added'],
                    "deleted": summary['deleted'],
                    "critical_items": summary['critical_items']
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
        
        report_rows, summary = generate_professional_report(blocks_a, blocks_b)
        
        # Build structured summary for AI
        critical_changes = [r for r in report_rows if r.get('is_critical', False)]
        other_changes = [r for r in report_rows if not r.get('is_critical', False)]
        
        changes_text = ""
        
        if critical_changes:
            changes_text += "CRITICAL CHANGES (requires immediate attention):\n"
            for i, change in enumerate(critical_changes[:20], 1):
                changes_text += f"{i}. {change['status']} - {change['comments']}\n"
                changes_text += f"   PDF 1: {change['pdf_a_content'][:80]}\n"
                changes_text += f"   PDF 2: {change['pdf_b_content'][:80]}\n\n"
        
        if other_changes:
            changes_text += "\nOTHER CHANGES (review for accuracy):\n"
            for i, change in enumerate(other_changes[:15], 1):
                changes_text += f"{i}. {change['status']} - {change['comments']}\n"
        
        if not changes_text:
            changes_text = "No changes detected between PDFs"
        
        qc_prompt = f"""Professional QC Report for packaging copy verification.

PDF COMPARISON ANALYSIS:
{changes_text}

SUMMARY STATISTICS:
- Total items reviewed: {summary['total_blocks']}
- Critical items: {summary['critical_items']}
- Modifications: {summary['modified']}
- Additions: {summary['added']}
- Deletions: {summary['deleted']}

Please format as a professional QC checklist with:
1. Clear item numbering
2. Checkbox format for verification
3. Status tags (CRITICAL, MODIFIED, ADDED, DELETED)
4. Action items
5. Summary at the end

Make it easy for a QC professional to print and verify each item."""

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
