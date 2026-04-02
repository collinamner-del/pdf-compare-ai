from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
import re
from typing import List, Dict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

def extract_text(file):
    """Simple, proven text extraction"""
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

def split_into_sentences(text):
    """Split text into sentences/blocks, keeping them together"""
    # Split by paragraph breaks first
    paragraphs = text.split('\n\n')
    
    sentences = []
    for para in paragraphs:
        if not para.strip():
            continue
        
        # Within each paragraph, split by sentence-ending punctuation
        # Keep punctuation with the sentence
        sent_list = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para.strip())
        
        for sent in sent_list:
            sent = sent.strip()
            if sent:
                # Don't split very short text - keep it together
                sentences.append(sent)
    
    return sentences

def identify_changes(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
    """Compare sentences and identify changes"""
    rows = []
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    
    row_id = 1
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i][:150],
                    "pdf_b_content": lines_a[i1 + i][:150],
                    "status": "NO CHANGE",
                    "comments": "Unchanged"
                })
                row_id += 1
        
        elif tag == 'replace':
            max_lines = max(i2 - i1, j2 - j1)
            for i in range(max_lines):
                a_line = lines_a[i1 + i] if i1 + i < i2 else ""
                b_line = lines_b[j1 + i] if j1 + i < j2 else ""
                
                # Clear DELETED indication
                if a_line and not b_line:
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": a_line[:150],
                        "pdf_b_content": "❌ [DELETED]",
                        "status": "DELETED",
                        "comments": "Content removed entirely"
                    })
                    row_id += 1
                
                # ADDED indication
                elif not a_line and b_line:
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block New",
                        "pdf_a_content": "",
                        "pdf_b_content": f"✅ **{b_line[:150]}**",
                        "status": "ADDED",
                        "comments": "New content added"
                    })
                    row_id += 1
                
                # MODIFIED
                elif a_line and b_line and a_line != b_line:
                    b_line_bold = highlight_differences(a_line, b_line)
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": f"Block {i1 + i + 1}",
                        "pdf_a_content": a_line[:150],
                        "pdf_b_content": b_line_bold[:150],
                        "status": "MODIFIED",
                        "comments": f"Changed"
                    })
                    row_id += 1
        
        elif tag == 'delete':
            for i in range(i2 - i1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block {i1 + i + 1}",
                    "pdf_a_content": lines_a[i1 + i][:150],
                    "pdf_b_content": "❌ [DELETED]",
                    "status": "DELETED",
                    "comments": "Content removed entirely"
                })
                row_id += 1
        
        elif tag == 'insert':
            for i in range(j2 - j1):
                rows.append({
                    "row_id": f"R{row_id}",
                    "tag": f"Block New",
                    "pdf_a_content": "",
                    "pdf_b_content": f"✅ **{lines_b[j1 + i][:150]}**",
                    "status": "ADDED",
                    "comments": "New content added"
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
    """Generate plain-language comment"""
    if not text_a:
        return f"Added: {text_b[:40]}"
    if not text_b:
        return f"Deleted: {text_a[:40]}"
    if text_a == text_b:
        return "Unchanged"
    return f"Modified"

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
    return jsonify({"status": "API running - Sentence-Aware"})

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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        # Use sentence-based splitting instead of line-based
        lines_a = split_into_sentences(text_a)
        lines_b = split_into_sentences(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Sentence-aware document comparison",
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
        
        text_a = extract_text(f1)
        text_b = extract_text(f2)
        
        if not text_a or not text_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        lines_a = split_into_sentences(text_a)
        lines_b = split_into_sentences(text_b)
        
        comparison_rows = identify_changes(lines_a, lines_b)
        
        # Build detailed changes list
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
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No significant changes detected"
        
        # Improved AI prompt with checkbox format
        qc_prompt = f"""You are a QC analyst. Create a professional checklist of changes from PDF 1 (original) to PDF 2 (updated).

CHANGES DETECTED:
{changes_text}

Format your response as a checklist. Each item should have:
- A checkbox [ ]
- PDF 1 content in quotes
- PDF 2 content in quotes
- Clear indication if DELETED with ❌ [DELETED]
- ACTION field (Verify/Confirm/Check)

Example format:
[ ] 1. PDF 1: "Original text here"   PDF 2: "Updated text here"   ACTION: Verify

[ ] 2. PDF 1: "Text to remove"   PDF 2: ❌ [DELETED]   ACTION: Confirm removal

Include only actual changes. Keep professional tone."""

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
