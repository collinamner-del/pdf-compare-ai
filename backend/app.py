from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
from typing import List, Dict, Tuple

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

def extract_text_with_regions(file):
    """Extract text while preserving spatial information and grouping"""
    try:
        with pdfplumber.open(file) as pdf:
            text_regions = []
            
            for page_num, page in enumerate(pdf.pages):
                # Get text with spatial info
                text_dict = page.extract_text_simple()
                
                # Also try to extract tables (nutrition tables, ingredient lists)
                tables = page.extract_tables()
                
                # Extract words with bounding boxes
                words = page.extract_words()
                
                if words:
                    # Group words into logical sections based on position
                    # Sort by Y position (top to bottom), then X position (left to right)
                    sorted_words = sorted(words, key=lambda w: (round(w['top'] / 20) * 20, w['left']))
                    
                    current_line = []
                    current_y = None
                    
                    for word in sorted_words:
                        word_y = round(word['top'] / 20) * 20  # Group words on same line
                        
                        # If we moved to a new line, save the current line
                        if current_y is not None and word_y != current_y:
                            if current_line:
                                text_regions.append(' '.join(current_line).strip())
                            current_line = []
                        
                        current_line.append(word['text'])
                        current_y = word_y
                    
                    # Add final line
                    if current_line:
                        text_regions.append(' '.join(current_line).strip())
                
                # Add tables as structured text
                if tables:
                    for table in tables:
                        for row in table:
                            row_text = ' | '.join([str(cell) if cell else '' for cell in row])
                            if row_text.strip():
                                text_regions.append(row_text)
                
                # Fallback to simple extraction if regions are empty
                if not text_regions:
                    simple_text = page.extract_text()
                    if simple_text:
                        text_regions.extend(simple_text.split('\n'))
            
            # Remove empty lines
            text_regions = [line.strip() for line in text_regions if line.strip()]
            return text_regions
    
    except Exception as e:
        raise Exception(f"Failed to extract text: {str(e)}")

def smart_compare_lines(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
    """Smart comparison that groups related changes"""
    rows = []
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    
    row_id = 1
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i][:100],
                    "pdf_b_content": lines_a[i1 + i][:100],
                    "status": "NO CHANGE",
                    "comments": "Unchanged"
                })
                row_id += 1
        
        elif tag == 'replace':
            max_lines = max(i2 - i1, j2 - j1)
            for i in range(max_lines):
                a_line = lines_a[i1 + i] if i1 + i < i2 else ""
                b_line = lines_b[j1 + i] if j1 + i < j2 else ""
                
                if a_line != b_line:
                    b_line_bold = highlight_differences(a_line, b_line)
                    status = "MODIFIED"
                else:
                    b_line_bold = b_line
                    status = "NO CHANGE"
                
                comment = generate_comment(a_line, b_line)
                
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": a_line[:100],
                    "pdf_b_content": b_line_bold[:100],
                    "status": status,
                    "comments": comment
                })
                row_id += 1
        
        elif tag == 'delete':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i][:100],
                    "pdf_b_content": "[DELETED]",
                    "status": "DELETED",
                    "comments": "Content removed"
                })
                row_id += 1
        
        elif tag == 'insert':
            for i in range(j2 - j1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"New Line",
                    "pdf_a_content": "",
                    "pdf_b_content": f"**{lines_b[j1 + i][:100]}**",
                    "status": "ADDED",
                    "comments": "New content"
                })
                row_id += 1
    
    return rows

def highlight_differences(text_a: str, text_b: str) -> str:
    """Bold only the changed parts"""
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

def generate_comment(text_a: str, text_b: str) -> str:
    """Generate brief comment about the change"""
    if not text_a:
        return f"Added: {text_b[:35]}"
    if not text_b:
        return f"Deleted: {text_a[:35]}"
    if text_a == text_b:
        return "Unchanged"
    return f"Modified: {text_a[:30]} to {text_b[:30]}"

def generate_summary(rows: List[Dict]) -> Dict:
    """Generate summary statistics"""
    statuses = {}
    for row in rows:
        status = row['status']
        statuses[status] = statuses.get(status, 0) + 1
    
    return {
        "total_rows": len(rows),
        "no_change": statuses.get('NO CHANGE', 0),
        "added": statuses.get('ADDED', 0),
        "deleted": statuses.get('DELETED', 0),
        "modified": statuses.get('MODIFIED', 0)
    }

@app.route("/")
def home():
    return jsonify({"status": "API running"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        lines_a = extract_text_with_regions(f1)
        lines_b = extract_text_with_regions(f2)
        
        comparison_rows = smart_compare_lines(lines_a, lines_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Smart region-based comparison of PDF A vs PDF B",
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
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        lines_a = extract_text_with_regions(f1)
        lines_b = extract_text_with_regions(f2)
        
        comparison_rows = smart_compare_lines(lines_a, lines_b)
        
        # Build change summary
        changes_list = []
        for row in comparison_rows:
            if row['status'] != 'NO CHANGE':
                changes_list.append({
                    'type': row['status'],
                    'location': row['tag'],
                    'original': row['pdf_a_content'][:50],
                    'updated': row['pdf_b_content'][:50],
                    'comment': row['comments']
                })
        
        changes_text = ""
        if changes_list:
            for change in changes_list:
                changes_text += f"- {change['type']}: {change['comment']}\n"
        
        qc_prompt = f"""You are a Document Quality Control Assistant reviewing food packaging updates.

Original Packaging (PDF 1):
{chr(10).join(lines_a[:50])}

Updated Packaging (PDF 2):
{chr(10).join(lines_b[:50])}

Changes Found:
{changes_text if changes_text else 'No changes detected'}

TASK: Write a clear, friendly QC summary for the quality control team.

Include:
1. Brief overview of changes
2. List each specific change with exact values (one per line with checkbox)
3. Areas that need verification
4. Any regulatory or compliance notes

Be specific with numbers and exact text. Use simple language. Format for printing."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": qc_prompt}],
            "temperature": 0.3,
            "max_tokens": 1800
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
