"""
PROFESSIONAL PACKAGING COPY AUDIT SYSTEM

Uses Visual QA audit framework for detailed side-by-side comparison
with intelligent extraction and precise difference detection.
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
# INTELLIGENT TEXT EXTRACTION
# ============================================================================

def extract_text_intelligently(file) -> str:
    """
    Extract text with proper grouping using multiple methods.
    Preserves structure and readability.
    """
    try:
        if hasattr(file, 'seek'):
            file.seek(0)
        
        with pdfplumber.open(file) as pdf:
            full_text = ""
            
            for page in pdf.pages:
                # Method 1: Try to get text with layout
                text = page.extract_text()
                
                if text:
                    full_text += text + "\n\n"
            
            return full_text
    
    except Exception as e:
        raise Exception(f"Text extraction failed: {str(e)}")

# ============================================================================
# INTELLIGENT BLOCK SEGMENTATION
# ============================================================================

class TextSegmenter:
    """Intelligently segments text into comparable blocks"""
    
    # Known section headers
    SECTION_PATTERNS = {
        'PRODUCT_NAME': r'^[A-Z][A-Za-z0-9\s&\-]{5,60}$',
        'INGREDIENTS': r'(?:ingredients?|composition|constituents)',
        'NUTRITION': r'(?:nutrition|nutritional|per 100)',
        'ALLERGENS': r'(?:allerg|may contain|contain)',
        'STORAGE': r'(?:stor|keep|temperature|fridge)',
        'INSTRUCTIONS': r'(?:instruction|direction|preparation|method)',
        'COMPANY': r'(?:made by|produced by|manufacturer)',
    }
    
    @staticmethod
    def segment(text: str) -> List[Dict]:
        """
        Segment text into logical blocks with metadata.
        Returns list of segments with content and type.
        """
        segments = []
        lines = text.split('\n')
        
        current_segment = {
            'type': 'GENERAL',
            'lines': [],
            'content': ''
        }
        
        for line in lines:
            line = line.rstrip()
            
            # Check if this is a section header
            section_type = TextSegmenter._detect_section(line)
            
            if section_type and current_segment['lines']:
                # Save current segment
                segments.append(TextSegmenter._finalize_segment(current_segment))
                current_segment = {'type': section_type, 'lines': [], 'content': ''}
            
            # Empty line = segment boundary
            if not line.strip():
                if current_segment['lines'] and len(current_segment['content']) > 20:
                    segments.append(TextSegmenter._finalize_segment(current_segment))
                    current_segment = {'type': 'GENERAL', 'lines': [], 'content': ''}
            else:
                current_segment['lines'].append(line)
                current_segment['content'] += line + ' '
        
        # Don't forget last segment
        if current_segment['lines'] and len(current_segment['content']) > 20:
            segments.append(TextSegmenter._finalize_segment(current_segment))
        
        return segments
    
    @staticmethod
    def _detect_section(line: str) -> Optional[str]:
        """Detect if line is a section header"""
        line_lower = line.lower().strip()
        
        for section_type, pattern in TextSegmenter.SECTION_PATTERNS.items():
            if re.search(pattern, line_lower):
                return section_type
        
        return None
    
    @staticmethod
    def _finalize_segment(segment: Dict) -> Dict:
        """Finalize segment with metadata"""
        content = ' '.join(segment['lines']).strip()
        content = ' '.join(content.split())  # Normalize whitespace
        
        return {
            'type': segment['type'],
            'content': content,
            'lines': len(segment['lines']),
            'chars': len(content)
        }

# ============================================================================
# INTELLIGENT SEGMENT MATCHING
# ============================================================================

class SegmentMatcher:
    """Matches segments across two documents intelligently"""
    
    def __init__(self, segments_a: List[Dict], segments_b: List[Dict]):
        self.segments_a = segments_a
        self.segments_b = segments_b
    
    def match(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Match segments using:
        1. Type matching (INGREDIENTS vs INGREDIENTS)
        2. Content similarity
        3. Position as tiebreaker
        
        Returns: (matches, deleted, added)
        """
        matched_pairs = []
        matched_b_indices = set()
        
        for idx_a, seg_a in enumerate(self.segments_a):
            best_match_idx = None
            best_similarity = 0
            
            for idx_b, seg_b in enumerate(self.segments_b):
                if idx_b in matched_b_indices:
                    continue
                
                # Type match bonus
                type_match = 1.0 if seg_a['type'] == seg_b['type'] else 0.3
                
                # Content similarity
                similarity = self._similarity(seg_a['content'], seg_b['content'])
                
                # Combined score
                combined = (similarity * 0.8) + (type_match * 100 * 0.2)
                
                if combined > best_similarity:
                    best_similarity = combined
                    best_match_idx = idx_b
            
            # Record match if strong enough
            if best_match_idx is not None and best_similarity > 50:
                # Perform detailed diff
                diff = self._detailed_diff(
                    seg_a['content'],
                    self.segments_b[best_match_idx]['content']
                )
                
                matched_pairs.append({
                    'seg_a': seg_a,
                    'seg_b': self.segments_b[best_match_idx],
                    'type': seg_a['type'],
                    'similarity': best_similarity,
                    'diff': diff
                })
                matched_b_indices.add(best_match_idx)
        
        # Unmatched
        matched_a_types = {m['type'] for m in matched_pairs}
        deleted = [
            s for s in self.segments_a 
            if s['type'] not in matched_a_types or len([
                m for m in matched_pairs if m['seg_a'] == s
            ]) == 0
        ]
        
        matched_b_types = {m['type'] for m in matched_pairs}
        added = [
            self.segments_b[i] for i in range(len(self.segments_b))
            if i not in matched_b_indices
        ]
        
        return matched_pairs, deleted, added
    
    def _similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity 0-100"""
        if not text_a or not text_b:
            return 0.0
        
        ratio = SequenceMatcher(None, text_a, text_b).ratio()
        return ratio * 100
    
    def _detailed_diff(self, text_a: str, text_b: str) -> Dict:
        """Create detailed diff"""
        if text_a == text_b:
            return {
                'status': 'IDENTICAL',
                'similarity': 100.0,
                'changes': []
            }
        
        similarity = (SequenceMatcher(None, text_a, text_b).ratio()) * 100
        
        # Extract specific changes
        changes = []
        
        matcher = SequenceMatcher(None, text_a, text_b)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                changes.append({
                    'type': 'MODIFIED',
                    'before': text_a[i1:i2].strip(),
                    'after': text_b[j1:j2].strip()
                })
            elif tag == 'delete':
                changes.append({
                    'type': 'DELETED',
                    'content': text_a[i1:i2].strip()
                })
            elif tag == 'insert':
                changes.append({
                    'type': 'ADDED',
                    'content': text_b[j1:j2].strip()
                })
        
        # Determine status
        if similarity >= 98:
            status = 'IDENTICAL'
        elif similarity >= 85:
            status = 'MINOR_CHANGE'
        else:
            status = 'SIGNIFICANT_CHANGE'
        
        return {
            'status': status,
            'similarity': round(similarity, 1),
            'changes': changes
        }

# ============================================================================
# FLASK ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    return jsonify({"status": "API running - Professional Audit System"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        if not f1 or not f1.filename or not f2 or not f2.filename:
            return jsonify({"error": "Files missing"}), 400
        
        # Extract text
        text_a = extract_text_intelligently(f1)
        text_b = extract_text_intelligently(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400
        
        # Segment intelligently
        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)
        
        if not segments_a or not segments_b:
            return jsonify({"error": "Could not segment text"}), 400
        
        # Match segments
        matcher = SegmentMatcher(segments_a, segments_b)
        matched_pairs, deleted, added = matcher.match()
        
        # Build report rows
        report_rows = []
        row_id = 1
        
        for match in matched_pairs:
            diff = match['diff']
            
            report_rows.append({
                "row_id": f"R{row_id}",
                "tag": match['type'],
                "pdf_a_content": match['seg_a']['content'][:150],
                "pdf_b_content": match['seg_b']['content'][:150],
                "status": diff['status'],
                "comments": f"{diff['similarity']:.0f}% match | {len(diff['changes'])} changes",
                "changes": diff['changes']
            })
            row_id += 1
        
        for seg in deleted:
            report_rows.append({
                "row_id": f"R{row_id}",
                "tag": seg['type'],
                "pdf_a_content": seg['content'][:150],
                "pdf_b_content": "❌ [DELETED]",
                "status": "DELETED",
                "comments": "Content removed"
            })
            row_id += 1
        
        for seg in added:
            report_rows.append({
                "row_id": f"R{row_id}",
                "tag": seg['type'],
                "pdf_a_content": "",
                "pdf_b_content": f"✅ {seg['content'][:150]}",
                "status": "ADDED",
                "comments": "New content"
            })
            row_id += 1
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Professional packaging copy audit",
                "comparison_table": report_rows,
                "summary": {
                    "total_rows": len(report_rows),
                    "identical": sum(1 for r in report_rows if r['status'] == 'IDENTICAL'),
                    "modified": sum(1 for r in report_rows if r['status'] in ['MINOR_CHANGE', 'SIGNIFICANT_CHANGE']),
                    "added": sum(1 for r in report_rows if r['status'] == 'ADDED'),
                    "deleted": sum(1 for r in report_rows if r['status'] == 'DELETED')
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
        
        text_a = extract_text_intelligently(f1)
        text_b = extract_text_intelligently(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text"}), 400
        
        segments_a = TextSegmenter.segment(text_a)
        segments_b = TextSegmenter.segment(text_b)
        
        matcher = SegmentMatcher(segments_a, segments_b)
        matched_pairs, deleted, added = matcher.match()
        
        # Build audit data for AI
        audit_data = "PACKAGING COPY AUDIT - DETAILED FINDINGS:\n\n"
        
        for i, match in enumerate(matched_pairs, 1):
            audit_data += f"{i}. {match['type']} SECTION\n"
            audit_data += f"   Version A: {match['seg_a']['content'][:120]}\n"
            audit_data += f"   Version B: {match['seg_b']['content'][:120]}\n"
            audit_data += f"   Similarity: {match['diff']['similarity']:.0f}%\n"
            
            if match['diff']['changes']:
                audit_data += f"   Changes detected:\n"
                for change in match['diff']['changes'][:5]:
                    if change['type'] == 'MODIFIED':
                        audit_data += f"   - MODIFIED: '{change['before']}' → '{change['after']}'\n"
                    elif change['type'] == 'DELETED':
                        audit_data += f"   - DELETED: '{change['content']}'\n"
                    elif change['type'] == 'ADDED':
                        audit_data += f"   - ADDED: '{change['content']}'\n"
            audit_data += "\n"
        
        if deleted:
            audit_data += f"\nDELETED SECTIONS:\n"
            for seg in deleted:
                audit_data += f"- {seg['type']}: {seg['content'][:100]}\n"
        
        if added:
            audit_data += f"\nADDED SECTIONS:\n"
            for seg in added:
                audit_data += f"- {seg['type']}: {seg['content'][:100]}\n"
        
        # Professional QA prompt using the framework
        qc_prompt = f"""### ROLE
You are a Senior Creative Director and Visual QA Specialist. Your goal is to perform a side-by-side audit of two packaging documents (Version A and Version B).

### OBJECTIVE
Detect every discrepancy from major content changes to subtle wording shifts.

### TASK HIERARCHY
1. **Copy Evolution Summary:** High-level overview of the packaging evolution. Is it clearer? More detailed? Changed?

2. **The "Spot the Difference" Table:**
    * **Copy Element:** (e.g., Product Name, Ingredients, Nutrition Facts, Allergens, Storage Instructions)
    * **Version A (Original):** Describe the copy in the original.
    * **Version B (Updated):** Describe the change in the revision.
    * **Impact:** (e.g., "Improves clarity," "Breaks compliance," "Better readability," "Subtle tweak")

3. **Technical Copy Audit:**
    * **Wording Changes:** Note shifts in terminology or phrasing.
    * **Numeric Values:** Note changes in weights, percentages, dates, or measurements.
    * **Compliance:** Identify any changes to allergen declarations, storage instructions, or legal text.

### RED FLAGS
- Point out any missing allergen information.
- Identify incomplete product information.
- Flag any brand voice inconsistencies.
- Note any values that changed significantly.

### EXECUTION
Analyze the copy hierarchy and provide your critique in a professional, scannable format.

AUDIT DATA:
{audit_data}

Please create a professional QC report with:
1. Executive Summary (2-3 sentences)
2. "Spot the Difference" Table (Visual Element | Version A | Version B | Impact)
3. Critical Findings (RED FLAGS section)
4. Action Items checklist

Format for easy printing and team review."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": qc_prompt}],
            "temperature": 0.4,
            "max_tokens": 3000
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
