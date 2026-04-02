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

def extract_text_with_columns(file) -> str:
    """Extract text respecting column layout, with fallback"""
    try:
        if hasattr(file, 'seek'):
            file.seek(0)
        
        with pdfplumber.open(file) as pdf:
            all_text = ""
            
            for page_num, page in enumerate(pdf.pages):
                try:
                    # Try smart column extraction
                    words = page.extract_words()
                    
                    if words and len(words) > 5:
                        try:
                            # Group words by Y position
                            lines = group_by_y_position(words)
                            
                            # Detect columns
                            columns = detect_columns_from_gaps(lines)
                            
                            # Reconstruct text
                            page_text = reconstruct_text_with_columns(columns)
                            all_text += page_text + "\n"
                        except Exception as e:
                            # Column detection failed, fallback to simple
                            print(f"Column detection failed on page {page_num}: {str(e)}")
                            text = page.extract_text()
                            if text:
                                all_text += text + "\n"
                    else:
                        # No words or too few words, use simple extraction
                        text = page.extract_text()
                        if text:
                            all_text += text + "\n"
                
                except Exception as e:
                    print(f"Page {page_num} extraction error: {str(e)}")
                    # Final fallback
                    try:
                        text = page.extract_text()
                        if text:
                            all_text += text + "\n"
                    except:
                        continue
            
            return all_text
    
    except Exception as e:
        raise Exception(f"Failed to extract text: {str(e)}")

def group_by_y_position(words, tolerance=3):
    """Group words that are on the same Y line (accounting for slight variations)"""
    lines = defaultdict(list)
    
    for word in words:
        # Round Y position to nearest tolerance (accounts for font size variations)
        y_key = round(word['top'] / tolerance) * tolerance
        lines[y_key].append(word)
    
    # Sort each line by X position (left to right)
    for y_key in lines:
        lines[y_key].sort(key=lambda w: w['x0'])
    
    # Return as list sorted by Y position (top to bottom)
    return [lines[y] for y in sorted(lines.keys())]

def detect_columns_from_gaps(lines, gap_threshold=20):
    """
    Detect column boundaries by finding large gaps between words.
    Simplified with better error handling.
    """
    try:
        # Find consistent gaps
        gap_positions = find_consistent_gaps(lines, gap_threshold)
        
        if not gap_positions or len(gap_positions) == 0:
            # No columns detected
            all_words = [w for line in lines for w in line]
            if all_words:
                return {0: all_words}
            return {}
        
        # Create column boundaries from gaps
        boundaries = [0] + sorted(gap_positions) + [10000]
        
        # Group words into columns
        columns = {}
        col_index = 0
        
        for line_words in lines:
            for word in line_words:
                try:
                    col = determine_column(word['x0'], boundaries)
                    if col not in columns:
                        columns[col] = []
                    columns[col].append(word)
                except Exception:
                    if 0 not in columns:
                        columns[0] = []
                    columns[0].append(word)
        
        return columns_to_text_blocks(columns)
    
    except Exception as e:
        print(f"Column detection error: {str(e)}")
        # Return single column fallback
        all_words = [w for line in lines for w in line]
        return {0: all_words}

def find_consistent_gaps(lines, threshold):
    """Find gaps that appear consistently across multiple lines"""
    try:
        gap_positions = defaultdict(int)
        
        for line_words in lines:
            if len(line_words) < 2:
                continue
            
            try:
                # Look at gaps between consecutive words
                for i in range(len(line_words) - 1):
                    try:
                        word1 = line_words[i]
                        word2 = line_words[i + 1]
                        
                        # Calculate gap
                        x1_end = word1.get('x1', word1.get('x0', 0))
                        x2_start = word2.get('x0', 0)
                        
                        gap = x2_start - x1_end
                        
                        if gap > threshold:
                            # Large gap found
                            gap_x = round((x1_end + x2_start) / 2)
                            gap_positions[gap_x] += 1
                    except Exception:
                        continue
            except Exception:
                continue
        
        # Return gaps that appear in multiple lines
        consistent = [x for x, count in gap_positions.items() if count >= 1]
        return consistent
    
    except Exception as e:
        print(f"Gap detection error: {str(e)}")
        return []

def determine_column(x_position, boundaries):
    """Given X position and column boundaries, determine which column it's in"""
    for i, boundary in enumerate(boundaries[1:]):
        if x_position < boundary:
            return i
    return len(boundaries) - 1

def single_column_structure(lines):
    """Treat all text as single column"""
    return {0: [word for line in lines for word in line]}

def columns_to_text_blocks(columns):
    """Convert columns of words back into readable text"""
    try:
        if not columns or not isinstance(columns, dict):
            return ""
        
        text_blocks = []
        
        # Sort columns by position
        sorted_columns = sorted(columns.items())
        
        for col_index, words_in_column in sorted_columns:
            if not words_in_column:
                continue
            
            try:
                # Sort words in column by Y position
                sorted_words = sorted(words_in_column, key=lambda w: w.get('top', 0))
                
                # Group by line within column
                column_lines = group_by_y_position(sorted_words, tolerance=3)
                
                # Convert to text
                column_text = []
                for line_words in column_lines:
                    line_text = ' '.join([w.get('text', '') for w in line_words if w.get('text')])
                    if line_text.strip():
                        column_text.append(line_text)
                
                if column_text:
                    text_blocks.append('\n'.join(column_text))
            except Exception as e:
                print(f"Column {col_index} processing error: {str(e)}")
                continue
        
        # Join columns
        if text_blocks:
            return '\n\n'.join(text_blocks)
        return ""
    
    except Exception as e:
        print(f"Columns to text error: {str(e)}")
        return ""

def reconstruct_text_with_columns(columns):
    """Reconstruct text from columns dictionary"""
    try:
        if not columns:
            return ""
        
        if isinstance(columns, dict):
            return columns_to_text_blocks(columns)
        else:
            # columns is already text
            return str(columns)
    except Exception as e:
        print(f"Text reconstruction error: {str(e)}")
        return ""

def split_into_blocks(text):
    """
    Split text into meaningful blocks:
    - Paragraphs (separated by blank lines)
    - Sentences (ending with . ! ?)
    - But preserve column structure
    """
    blocks = []
    
    # Split by multiple newlines first (column/section breaks)
    sections = text.split('\n\n')
    
    for section in sections:
        if not section.strip():
            continue
        
        # Within each section, split by sentences
        # But be careful with abbreviations and decimals
        sentences = split_by_sentence(section)
        blocks.extend(sentences)
    
    return blocks

def split_by_sentence(text):
    """Split text by sentence-ending punctuation, respecting abbreviations"""
    if not text.strip():
        return []
    
    # Use regex to split on . ! ? but not on abbreviations
    # Simplified: split on period followed by space and capital letter
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    
    # Also split on double newlines within the text
    result = []
    for sent in sentences:
        if '\n\n' in sent:
            result.extend(sent.split('\n\n'))
        else:
            result.append(sent)
    
    return [s.strip() for s in result if s.strip()]

def identify_changes(blocks_a: List[str], blocks_b: List[str]) -> List[Dict]:
    """Compare blocks and identify changes"""
    rows = []
    matcher = difflib.SequenceMatcher(None, blocks_a, blocks_b)
    
    row_id = 1
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block {i1 + i + 1}",
                    "pdf_a_content": blocks_a[i1 + i][:150],
                    "pdf_b_content": blocks_a[i1 + i][:150],
                    "status": "NO CHANGE",
                    "comments": "Unchanged"
                })
                row_id += 1
        
        elif tag == 'replace':
            max_blocks = max(i2 - i1, j2 - j1)
            for i in range(max_blocks):
                a_block = blocks_a[i1 + i] if i1 + i < i2 else ""
                b_block = blocks_b[j1 + i] if j1 + i < j2 else ""
                
                if a_block and not b_block:
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": a_block[:150],
                        "pdf_b_content": "❌ [DELETED]",
                        "status": "DELETED",
                        "comments": "Content removed"
                    })
                
                elif not a_block and b_block:
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block New",
                        "pdf_a_content": "",
                        "pdf_b_content": f"✅ **{b_block[:150]}**",
                        "status": "ADDED",
                        "comments": "New content"
                    })
                
                elif a_block and b_block and a_block != b_block:
                    b_bold = highlight_differences(a_block, b_block)
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": a_block[:150],
                        "pdf_b_content": b_bold[:150],
                        "status": "MODIFIED",
                        "comments": "Changed"
                    })
                else:
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": a_block[:150],
                        "pdf_b_content": a_block[:150],
                        "status": "NO CHANGE",
                        "comments": "Unchanged"
                    })
                
                row_id += 1
        
        elif tag == 'delete':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block {i1 + i + 1}",
                    "pdf_a_content": blocks_a[i1 + i][:150],
                    "pdf_b_content": "❌ [DELETED]",
                    "status": "DELETED",
                    "comments": "Content removed"
                })
                row_id += 1
        
        elif tag == 'insert':
            for i in range(j2 - j1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block New",
                    "pdf_a_content": "",
                    "pdf_b_content": f"✅ **{blocks_b[j1 + i][:150]}**",
                    "status": "ADDED",
                    "comments": "New content"
                })
                row_id += 1
    
    return rows

def highlight_differences(text_a: str, text_b: str) -> str:
    """Highlight changed parts with bold"""
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

def generate_summary(rows: List[Dict]) -> Dict:
    """Generate summary statistics"""
    statuses = {}
    for row in rows:
        status = row['status']
        statuses[status] = statuses.get(status, 0) + 1
    
    return {
        "total_blocks": len(rows),
        "no_change": statuses.get('NO CHANGE', 0),
        "added": statuses.get('ADDED', 0),
        "deleted": statuses.get('DELETED', 0),
        "modified": statuses.get('MODIFIED', 0)
    }

@app.route("/")
def home():
    return jsonify({"status": "API running - Column-Aware Extraction"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        if not f1 or not f1.filename:
            return jsonify({"error": "File 1 is missing"}), 400
        if not f2 or not f2.filename:
            return jsonify({"error": "File 2 is missing"}), 400
        
        if not f1.filename.lower().endswith('.pdf'):
            return jsonify({"error": "File 1 must be a PDF"}), 400
        if not f2.filename.lower().endswith('.pdf'):
            return jsonify({"error": "File 2 must be a PDF"}), 400
        
        # Use column-aware extraction
        text_a = extract_text_with_columns(f1)
        text_b = extract_text_with_columns(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        # Split into meaningful blocks
        blocks_a = split_into_blocks(text_a)
        blocks_b = split_into_blocks(text_b)
        
        comparison_rows = identify_changes(blocks_a, blocks_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Column-aware document comparison",
                "comparison_table": comparison_rows,
                "summary": summary
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
        
        if not f1 or not f1.filename:
            return jsonify({"error": "File 1 is missing"}), 400
        if not f2 or not f2.filename:
            return jsonify({"error": "File 2 is missing"}), 400
        
        if not f1.filename.lower().endswith('.pdf'):
            return jsonify({"error": "File 1 must be a PDF"}), 400
        if not f2.filename.lower().endswith('.pdf'):
            return jsonify({"error": "File 2 must be a PDF"}), 400
        
        text_a = extract_text_with_columns(f1)
        text_b = extract_text_with_columns(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        blocks_a = split_into_blocks(text_a)
        blocks_b = split_into_blocks(text_b)
        
        comparison_rows = identify_changes(blocks_a, blocks_b)
        
        # Build detailed changes
        important_changes = [r for r in comparison_rows if r.get('status') != 'NO CHANGE']
        
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
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No significant changes"
        
        qc_prompt = f"""You are a QC analyst. Create a professional checklist of changes from PDF 1 (original) to PDF 2 (updated).

CHANGES DETECTED:
{changes_text}

Format as a checkbox list. Each item:
[ ] PDF 1: "..."   PDF 2: "..."   ACTION: Verify

Include only actual changes. Be specific and professional."""

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
