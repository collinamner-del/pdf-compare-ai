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

def safe_extract_text(file) -> List[str]:
    """Extract text with comprehensive error handling"""
    lines = []
    
    try:
        with pdfplumber.open(file) as pdf:
            for page_num, page in enumerate(pdf.pages):
                try:
                    # Method 1: Try word-based extraction
                    words = page.extract_words()
                    if words:
                        sorted_words = sorted(words, key=lambda w: (round(w['top'] / 20) * 20, w['left']))
                        current_line = []
                        current_y = None
                        
                        for word in sorted_words:
                            word_y = round(word['top'] / 20) * 20
                            if current_y is not None and word_y != current_y:
                                if current_line:
                                    lines.append(' '.join(current_line))
                                current_line = []
                            current_line.append(word['text'])
                            current_y = word_y
                        
                        if current_line:
                            lines.append(' '.join(current_line))
                    
                    # Method 2: Fallback to simple text extraction
                    if not lines:
                        text = page.extract_text()
                        if text:
                            for line in text.split('\n'):
                                if line.strip():
                                    lines.append(line.strip())
                
                except Exception as e:
                    print(f"Error on page {page_num}: {str(e)}")
                    continue
        
        return [l.strip() for l in lines if l.strip()]
    
    except Exception as e:
        raise Exception(f"PDF extraction failed: {str(e)}")

def compare_texts(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
    """Simple, reliable text comparison"""
    rows = []
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    row_id = 1
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        try:
            if tag == 'equal':
                for i in range(i2 - i1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": "GENERAL",
                        "line": f"L{i1 + i + 1}",
                        "pdf_a_content": lines_a[i1 + i][:100],
                        "pdf_b_content": lines_a[i1 + i][:100],
                        "data_type": "TEXT",
                        "status": "NO CHANGE",
                        "significance": "low",
                        "comments": "Unchanged"
                    })
                    row_id += 1
            
            elif tag == 'replace':
                max_lines = max(i2 - i1, j2 - j1)
                for i in range(max_lines):
                    a_line = lines_a[i1 + i] if i1 + i < i2 else ""
                    b_line = lines_b[j1 + i] if j1 + i < j2 else ""
                    
                    if a_line != b_line:
                        # Highlight differences
                        matcher_inner = difflib.SequenceMatcher(None, a_line, b_line)
                        b_display = ""
                        for tag_inner, i1_inner, i2_inner, j1_inner, j2_inner in matcher_inner.get_opcodes():
                            if tag_inner == 'equal':
                                b_display += b_line[j1_inner:j2_inner]
                            else:
                                b_display += f"**{b_line[j1_inner:j2_inner]}**"
                        
                        rows.append({
                            "row_id": f"R{row_id}",
                            "tag": "GENERAL",
                            "line": f"L{i1 + i + 1}",
                            "pdf_a_content": a_line[:100],
                            "pdf_b_content": b_display[:100],
                            "data_type": "TEXT",
                            "status": "MODIFIED",
                            "significance": "high",
                            "comments": f"Modified content"
                        })
                        row_id += 1
                    else:
                        rows.append({
                            "row_id": f"R{row_id}",
                            "tag": "GENERAL",
                            "line": f"L{i1 + i + 1}",
                            "pdf_a_content": a_line[:100],
                            "pdf_b_content": a_line[:100],
                            "data_type": "TEXT",
                            "status": "NO CHANGE",
                            "significance": "low",
                            "comments": "Unchanged"
                        })
                        row_id += 1
            
            elif tag == 'delete':
                for i in range(i2 - i1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": "GENERAL",
                        "line": f"L{i1 + i + 1}",
                        "pdf_a_content": lines_a[i1 + i][:100],
                        "pdf_b_content": "[DELETED]",
                        "data_type": "TEXT",
                        "status": "DELETED",
                        "significance": "high",
                        "comments": "Content removed"
                    })
                    row_id += 1
            
            elif tag == 'insert':
                for i in range(j2 - j1):
                    rows.append({
                        "row_id": f"R{row_id}",
                        "tag": "GENERAL",
                        "line": f"New",
                        "pdf_a_content": "",
                        "pdf_b_content": f"**{lines_b[j1 + i][:100]}**",
                        "data_type": "TEXT",
                        "status": "ADDED",
                        "significance": "high",
                        "comments": "New content"
                    })
                    row_id += 1
        
        except Exception as e:
            print(f"Error processing comparison: {str(e)}")
            continue
    
    return rows

def get_summary(rows: List[Dict]) -> Dict:
    """Generate summary counts"""
    try:
        no_change = sum(1 for r in rows if r.get('status') == 'NO CHANGE')
        modified = sum(1 for r in rows if r.get('status') == 'MODIFIED')
        added = sum(1 for r in rows if r.get('status') == 'ADDED')
        deleted = sum(1 for r in rows if r.get('status') == 'DELETED')
        
        return {
            "total_rows": len(rows),
            "no_change": no_change,
            "modified": modified,
            "added": added,
            "deleted": deleted,
            "critical_changes": modified + added + deleted
        }
    except Exception:
        return {"total_rows": len(rows), "error": "Could not generate summary"}

@app.route("/")
def home():
    return jsonify({"status": "API running - Production v1"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both PDF files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        # Extract text
        lines_a = safe_extract_text(f1)
        lines_b = safe_extract_text(f2)
        
        if not lines_a or not lines_b:
            return jsonify({"error": "Could not extract text from one or both PDFs"}), 400
        
        # Compare
        comparison_rows = compare_texts(lines_a, lines_b)
        summary = get_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Document comparison analysis",
                "comparison_table": comparison_rows,
                "summary": summary
            }
        })
    
    except Exception as e:
        print(f"Compare endpoint error: {str(e)}")
        return jsonify({"error": f"Comparison failed: {str(e)}"}), 500

@app.route("/summary", methods=["POST"])
def summary():
    try:
        if not OPENAI_API_KEY:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        # Extract text
        lines_a = safe_extract_text(f1)
        lines_b = safe_extract_text(f2)
        
        if not lines_a or not lines_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        # Compare
        comparison_rows = compare_texts(lines_a, lines_b)
        
        # Find important changes
        important_changes = [r for r in comparison_rows if r.get('status') in ['MODIFIED', 'ADDED', 'DELETED']]
        
        # Build detailed changes list showing PDF 1 → PDF 2
        changes_detail = []
        for change in important_changes[:25]:
            status = change.get('status', '')
            pdf1 = change.get('pdf_a_content', '')
            pdf2 = change.get('pdf_b_content', '').replace('**', '')
            line = change.get('line', '')
            
            if status == 'MODIFIED':
                changes_detail.append(f"Line {line}: PDF 1 '{pdf1}' → PDF 2 '{pdf2}'")
            elif status == 'ADDED':
                changes_detail.append(f"Line {line}: ADDED in PDF 2 '{pdf2}'")
            elif status == 'DELETED':
                changes_detail.append(f"Line {line}: REMOVED from PDF 2 (was '{pdf1}')")
        
        changes_text = "\n".join(changes_detail) if changes_detail else "No significant changes detected"
        
        # Create AI prompt - ask for specific format
        prompt = f"""You are a QC analyst. Create a professional checklist of changes from PDF 1 (original) to PDF 2 (updated).

CHANGES DETECTED:
{changes_text}

Format your response EXACTLY like this example:
Updates Required:

1. [ ] PDF 1: "Old text here"
   PDF 2: "New text here"
   ACTION: Verify/Check

2. [ ] PDF 1: "Text removed"
   PDF 2: [DELETED]
   ACTION: Confirm removal

3. [ ] PDF 1: [NEW]
   PDF 2: "New text added"
   ACTION: Verify

Include only actual changes. Be specific with exact text. Keep professional tone."""
        
        # Call OpenAI
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1500
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            error_text = response.text if response.text else "Unknown error"
            return jsonify({"error": f"OpenAI API error: {error_text}"}), 500
        
        result = response.json()
        summary_text = result["choices"][0]["message"]["content"]
        
        return jsonify({"summary": summary_text})
    
    except Exception as e:
        print(f"Summary endpoint error: {str(e)}")
        return jsonify({"error": f"Summary failed: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
