from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
from typing import List, Dict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

def extract_text(file):
    try:
        with pdfplumber.open(file) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except Exception as e:
        raise Exception(f"Failed to extract text: {str(e)}")

def split_into_lines(text):
    """Split text into lines, preserving structure"""
    return [line for line in text.split('\n')]

def identify_changes(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
    """Compare two sets of lines and identify changes"""
    rows = []
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    
    row_id = 1
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i],
                    "pdf_b_content": lines_a[i1 + i],
                    "status": "NO CHANGE",
                    "comments": "Content unchanged"
                })
                row_id += 1
        
        elif tag == 'replace':
            max_lines = max(i2 - i1, j2 - j1)
            for i in range(max_lines):
                a_line = lines_a[i1 + i] if i1 + i < i2 else ""
                b_line = lines_b[j1 + i] if j1 + i < j2 else ""
                
                if a_line != b_line:
                    b_line_bold = highlight_differences(a_line, b_line)
                else:
                    b_line_bold = b_line
                
                comment = generate_comment(a_line, b_line)
                
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": a_line,
                    "pdf_b_content": b_line_bold,
                    "status": "MODIFIED" if a_line != b_line else "NO CHANGE",
                    "comments": comment
                })
                row_id += 1
        
        elif tag == 'delete':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i],
                    "pdf_b_content": "[REMOVED]",
                    "status": "REMOVED",
                    "comments": "Content removed in PDF B"
                })
                row_id += 1
        
        elif tag == 'insert':
            for i in range(j2 - j1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"New Line",
                    "pdf_a_content": "",
                    "pdf_b_content": f"**{lines_b[j1 + i]}**",
                    "status": "ADDED",
                    "comments": "New content added in PDF B"
                })
                row_id += 1
    
    return rows

def highlight_differences(text_a: str, text_b: str) -> str:
    """Bold only the changed characters/words"""
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
    """Generate plain-language comment about the change"""
    if not text_a:
        return f"Added: {text_b[:40]}"
    if not text_b:
        return f"Removed: {text_a[:40]}"
    if text_a == text_b:
        return "No change"
    return f"Modified: {text_a[:35]} → {text_b[:35]}"

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
        "removed": statuses.get('REMOVED', 0),
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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        lines_a = split_into_lines(text_a)
        lines_b = split_into_lines(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Line-by-line comparison of PDF A vs PDF B",
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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        lines_a = split_into_lines(text_a)
        lines_b = split_into_lines(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        
        # Build detailed change summary for AI
        changes_summary = []
        for row in comparison_rows:
            if row['status'] != 'NO CHANGE':
                changes_summary.append(f"[{row['status']}] Line {row['tag']}: {row['comments']}")
        
        # QC Checklist Prompt
        qc_prompt = f"""You are a Document QC Analyst creating a COMPREHENSIVE CHECKLIST of ALL changes found between two documents.

DOCUMENT A (Original):
{text_a[:4000]}

DOCUMENT B (Revised):
{text_b[:4000]}

DETECTED CHANGES (from line-by-line analysis):
{chr(10).join(changes_summary)}

TASK: Create a detailed QC checklist for manual review. For EACH change detected:
1. List the exact change with before/after values
2. Use this format: ☐ [TYPE] Location: Original → New | Status: (VERIFY/APPROVED/FLAGGED)
3. Group by change type (MODIFIED, ADDED, REMOVED)
4. Be specific with values, not vague descriptions
5. Include line numbers or context
6. Format for printing/manual checkoff

OUTPUT STRUCTURE:
**MODIFIED ITEMS** (if any)
☐ [Item description] | Original: X → New: Y

**ADDED ITEMS** (if any)
☐ [New content description]

**REMOVED ITEMS** (if any)
☐ [Removed content description]

**SUMMARY COUNTS**
Total Changes: X | Modified: X | Added: X | Removed: X

Be concise, professional, and use EXACT VALUES from the documents."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "user",
                    "content": qc_prompt
                }
            ],
            "temperature": 0.2,
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
