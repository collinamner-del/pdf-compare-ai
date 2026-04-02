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
    """Simple, proven text extraction"""
    try:
        # Reset file pointer
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
                    "pdf_a_content": lines_a[i1 + i][:120],
                    "pdf_b_content": lines_a[i1 + i][:120],
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
                else:
                    b_line_bold = b_line
                
                comment = generate_comment(a_line, b_line)
                
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": a_line[:120],
                    "pdf_b_content": b_line_bold[:120],
                    "status": "MODIFIED" if a_line != b_line else "NO CHANGE",
                    "comments": comment
                })
                row_id += 1
        
        elif tag == 'delete':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Line {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i][:120],
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
                    "pdf_b_content": f"**{lines_b[j1 + i][:120]}**",
                    "status": "ADDED",
                    "comments": "New content"
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
        return f"Deleted: {text_a[:40]}"
    if text_a == text_b:
        return "Unchanged"
    return f"Modified: {text_a[:35]} to {text_b[:35]}"

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
        # Better file validation
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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        lines_a = split_into_lines(text_a)
        lines_b = split_into_lines(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Document comparison analysis",
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
        
        # Better file validation
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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        lines_a = split_into_lines(text_a)
        lines_b = split_into_lines(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        
        # Build detailed change summary - IMPROVED FORMAT
        important_changes = [r for r in comparison_rows if r.get('status') in ['MODIFIED', 'ADDED', 'DELETED']]
        
        changes_detail = []
        for change in important_changes[:25]:
            status = change.get('status', '')
            pdf1 = change.get('pdf_a_content', '')
            pdf2 = change.get('pdf_b_content', '').replace('**', '')
            line = change.get('tag', '')
            
            if status == 'MODIFIED':
                changes_detail.append(f"{line}: PDF 1 '{pdf1}' → PDF 2 '{pdf2}'")
            elif status == 'ADDED':
                changes_detail.append(f"{line}: ADDED '{pdf2}'")
            elif status == 'DELETED':
                changes_detail.append(f"{line}: DELETED '{pdf1}'")
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No significant changes detected"
        
        # IMPROVED AI PROMPT
        qc_prompt = f"""You are a QC analyst. Create a professional checklist of changes from PDF 1 (original) to PDF 2 (updated).

CHANGES DETECTED:
{changes_text}

Format your response EXACTLY like this example:

Updates Required:

1. [ ] PDF 1: "Old text here"
   PDF 2: "New text here"
   ACTION: Verify

2. [ ] PDF 1: [DELETED]
   PDF 2: "Replacement text"
   ACTION: Confirm

Include only actual changes. Be specific with exact text. Keep professional tone."""

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
