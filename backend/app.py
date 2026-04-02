from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
import re
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Common packaging section headers
SECTION_KEYWORDS = {
    'INGREDIENTS': r'ingredient|composition|constituents',
    'NUTRITION': r'nutrition|nutritional|per 100|energy|fat|protein|carbohydrate',
    'ALLERGENS': r'allerg|contain|may contain|traces?|gluten|dairy|nuts|peanuts',
    'STORAGE': r'stor|keep|temperature|fridge|freezer|shelf life|best before|use by',
    'INSTRUCTIONS': r'instruction|direction|preparation|how to|method|cook|serving',
    'WARNINGS': r'warning|caution|danger|risk|contact|medical|poison',
    'COMPANY': r'made by|produced by|manufacturer|company|address|contact'
}

class TextProcessor:
    """Intelligent text processing with OCR awareness"""
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for better comparison"""
        try:
            if not text:
                return ""
            # Remove extra whitespace but preserve structure
            text = ' '.join(text.split())
            # Normalize common OCR errors
            text = text.replace('l3', '13')
            text = text.replace('O0', '00')
            text = text.replace('S5', '55')
            return text.strip()
        except Exception:
            return str(text).strip() if text else ""
    
    @staticmethod
    def detect_data_type(text: str) -> str:
        """Detect what type of data this is"""
        try:
            if not text:
                return 'TEXT'
            
            text = text.strip()
            
            if re.match(r'^\d+([.,]\d+)?\s*(g|kg|mg|ml|l|oz|lb)$', text, re.I):
                return 'WEIGHT'
            elif re.match(r'^\d+([.,]\d+)?%$', text):
                return 'PERCENTAGE'
            elif re.match(r'^\d+([.,]\d+)?\s*(kcal|kj|cal)', text, re.I):
                return 'ENERGY'
            elif re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$', text):
                return 'DATE'
            elif re.match(r'^[0-9]{13}$', text):
                return 'BARCODE'
            elif text.lower() in ['may contain', 'contains', 'allergen', 'warning']:
                return 'ALLERGEN'
            elif any(keyword in text.lower() for keyword in ['°c', 'fridge', 'freeze', 'cool']):
                return 'TEMPERATURE'
            else:
                return 'TEXT'
        except Exception:
            return 'TEXT'
    
    @staticmethod
    def extract_sections(lines: List[str]) -> Dict[str, List[str]]:
        """Intelligently group text into sections"""
        try:
            sections = defaultdict(list)
            current_section = 'GENERAL'
            
            for line in lines:
                if not line or not line.strip():
                    continue
                
                # Check if this line is a section header
                line_lower = line.lower()
                for section_name, pattern in SECTION_KEYWORDS.items():
                    try:
                        if re.search(pattern, line_lower):
                            current_section = section_name
                            break
                    except Exception:
                        continue
                
                sections[current_section].append(line)
            
            return dict(sections)
        except Exception:
            return {'GENERAL': lines}

class SmartComparator:
    """Intelligent comparison engine"""
    
    @staticmethod
    def fuzzy_match(text_a: str, text_b: str, threshold: float = 0.85) -> Tuple[bool, float]:
        """Fuzzy matching for similar text"""
        try:
            if not text_a or not text_b:
                return False, 0.0
            ratio = difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
            return ratio >= threshold, ratio
        except Exception:
            return False, 0.0
    
    @staticmethod
    def extract_number(text: str) -> Optional[float]:
        """Safely extract a number from text"""
        try:
            if not text:
                return None
            matches = re.findall(r'\d+(?:[.,]\d+)?', text)
            if matches:
                return float(matches[0].replace(',', '.'))
            return None
        except Exception:
            return None
    
    @staticmethod
    def is_significant_change(original: str, updated: str, data_type: str) -> bool:
        """Determine if a change is significant"""
        try:
            # All allergen changes are critical
            if data_type == 'ALLERGEN':
                return True
            
            # Numbers - flag if change is >5%
            if data_type in ['WEIGHT', 'PERCENTAGE', 'ENERGY']:
                orig_num = SmartComparator.extract_number(original)
                upd_num = SmartComparator.extract_number(updated)
                
                if orig_num and upd_num and orig_num != 0:
                    percent_change = abs(upd_num - orig_num) / orig_num * 100
                    return percent_change > 5
                elif orig_num != upd_num:
                    return True
            
            # Default: all changes are significant
            return True
        except Exception:
            return True
    
    @staticmethod
    def smart_compare_lines(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
        """Compare with intelligence"""
        try:
            processor = TextProcessor()
            rows = []
            
            # Extract sections for better organization
            sections_a = processor.extract_sections(lines_a)
            sections_b = processor.extract_sections(lines_b)
            
            all_sections = set(sections_a.keys()) | set(sections_b.keys())
            row_id = 1
            
            for section in sorted(all_sections):
                section_lines_a = sections_a.get(section, [])
                section_lines_b = sections_b.get(section, [])
                
                matcher = difflib.SequenceMatcher(None, section_lines_a, section_lines_b)
                
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == 'equal':
                        for i in range(i2 - i1):
                            try:
                                line = section_lines_a[i1 + i] if i1 + i < len(section_lines_a) else ""
                                rows.append({
                                    "row_id": f"R{row_id}",
                                    "tag": section,
                                    "line": f"Line {i1 + i + 1}",
                                    "pdf_a_content": processor.normalize_text(line)[:120],
                                    "pdf_b_content": processor.normalize_text(line)[:120],
                                    "data_type": processor.detect_data_type(line),
                                    "status": "NO CHANGE",
                                    "significance": "low",
                                    "comments": "Unchanged"
                                })
                                row_id += 1
                            except Exception:
                                continue
                    
                    elif tag == 'replace':
                        max_lines = max(i2 - i1, j2 - j1)
                        for i in range(max_lines):
                            try:
                                a_line = section_lines_a[i1 + i] if i1 + i < len(section_lines_a) else ""
                                b_line = section_lines_b[j1 + i] if j1 + i < len(section_lines_b) else ""
                                
                                a_norm = processor.normalize_text(a_line)
                                b_norm = processor.normalize_text(b_line)
                                
                                if a_norm != b_norm:
                                    is_similar, similarity = SmartComparator.fuzzy_match(a_norm, b_norm)
                                    
                                    if is_similar:
                                        status = "MINOR_VARIATION"
                                        b_display = b_norm
                                    else:
                                        status = "MODIFIED"
                                        b_display = SmartComparator.highlight_differences(a_norm, b_norm)
                                    
                                    data_type = processor.detect_data_type(a_norm or b_norm)
                                    is_significant = SmartComparator.is_significant_change(a_norm, b_norm, data_type)
                                    
                                    rows.append({
                                        "row_id": f"R{row_id}",
                                        "tag": section,
                                        "line": f"Line {i1 + i + 1}",
                                        "pdf_a_content": a_norm[:120],
                                        "pdf_b_content": b_display[:120],
                                        "data_type": data_type,
                                        "status": status,
                                        "significance": "high" if is_significant else "low",
                                        "comments": SmartComparator.generate_smart_comment(a_norm, b_norm, data_type)
                                    })
                                    row_id += 1
                                else:
                                    rows.append({
                                        "row_id": f"R{row_id}",
                                        "tag": section,
                                        "line": f"Line {i1 + i + 1}",
                                        "pdf_a_content": a_norm[:120],
                                        "pdf_b_content": a_norm[:120],
                                        "data_type": processor.detect_data_type(a_norm),
                                        "status": "NO CHANGE",
                                        "significance": "low",
                                        "comments": "Unchanged"
                                    })
                                    row_id += 1
                            except Exception:
                                continue
                    
                    elif tag == 'delete':
                        for i in range(i2 - i1):
                            try:
                                line = section_lines_a[i1 + i] if i1 + i < len(section_lines_a) else ""
                                data_type = processor.detect_data_type(line)
                                
                                rows.append({
                                    "row_id": f"R{row_id}",
                                    "tag": section,
                                    "line": f"Line {i1 + i + 1}",
                                    "pdf_a_content": processor.normalize_text(line)[:120],
                                    "pdf_b_content": "[DELETED]",
                                    "data_type": data_type,
                                    "status": "DELETED",
                                    "significance": "high" if data_type in ['ALLERGEN', 'WEIGHT', 'ENERGY'] else "medium",
                                    "comments": f"Deleted {data_type.lower()}"
                                })
                                row_id += 1
                            except Exception:
                                continue
                    
                    elif tag == 'insert':
                        for i in range(j2 - j1):
                            try:
                                line = section_lines_b[j1 + i] if j1 + i < len(section_lines_b) else ""
                                data_type = processor.detect_data_type(line)
                                
                                rows.append({
                                    "row_id": f"R{row_id}",
                                    "tag": section,
                                    "line": f"New Line",
                                    "pdf_a_content": "",
                                    "pdf_b_content": f"**{processor.normalize_text(line)[:120]}**",
                                    "data_type": data_type,
                                    "status": "ADDED",
                                    "significance": "high" if data_type in ['ALLERGEN', 'WEIGHT', 'ENERGY'] else "medium",
                                    "comments": f"Added {data_type.lower()}"
                                })
                                row_id += 1
                            except Exception:
                                continue
            
            return rows
        except Exception as e:
            return [{"row_id": "ERROR", "tag": "ERROR", "line": "ERROR", 
                    "pdf_a_content": str(e), "pdf_b_content": "", 
                    "data_type": "ERROR", "status": "ERROR", 
                    "significance": "high", "comments": str(e)}]
    
    @staticmethod
    def highlight_differences(text_a: str, text_b: str) -> str:
        """Highlight only changed parts"""
        try:
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
        except Exception:
            return text_b
    
    @staticmethod
    def generate_smart_comment(text_a: str, text_b: str, data_type: str) -> str:
        """Generate intelligent comments"""
        try:
            if data_type == 'PERCENTAGE':
                a_val = SmartComparator.extract_number(text_a)
                b_val = SmartComparator.extract_number(text_b)
                if a_val is not None and b_val is not None:
                    change = b_val - a_val
                    return f"{data_type}: {a_val}% to {b_val}% (change: {change:+.1f}%)"
            
            elif data_type == 'WEIGHT':
                return f"{data_type}: {text_a} to {text_b}"
            
            elif data_type == 'ALLERGEN':
                return f"⚠ CRITICAL - Allergen info changed"
            
            elif data_type == 'DATE':
                return f"Date: {text_a} to {text_b}"
            
            else:
                return f"{data_type} modified"
        except Exception:
            return f"{data_type} modified"

def extract_enhanced_text(file) -> List[str]:
    """Extract text with all enhancements"""
    try:
        with pdfplumber.open(file) as pdf:
            all_lines = []
            
            for page in pdf.pages:
                try:
                    # Try to extract tables first
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                try:
                                    row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                                    if row_text.strip():
                                        all_lines.append(row_text)
                                except Exception:
                                    continue
                except Exception:
                    pass
                
                try:
                    # Then extract regular text with spatial awareness
                    words = page.extract_words()
                    if words and len(words) > 0:
                        sorted_words = sorted(words, key=lambda w: (round(w['top'] / 20) * 20, w['left']))
                        
                        current_line = []
                        current_y = None
                        
                        for word in sorted_words:
                            try:
                                word_y = round(word['top'] / 20) * 20
                                
                                if current_y is not None and word_y != current_y:
                                    if current_line:
                                        all_lines.append(' '.join(current_line).strip())
                                    current_line = []
                                
                                current_line.append(word['text'])
                                current_y = word_y
                            except Exception:
                                continue
                        
                        if current_line:
                            all_lines.append(' '.join(current_line).strip())
                except Exception:
                    pass
                
                # Fallback to simple extraction
                try:
                    if len(all_lines) == 0:
                        simple_text = page.extract_text()
                        if simple_text:
                            all_lines.extend([line.strip() for line in simple_text.split('\n') if line.strip()])
                except Exception:
                    pass
            
            return [line for line in all_lines if line and line.strip()]
    
    except Exception as e:
        raise Exception(f"Text extraction failed: {str(e)}")

def generate_summary(rows: List[Dict]) -> Dict:
    """Generate comprehensive summary"""
    try:
        statuses = defaultdict(int)
        significance_counts = defaultdict(int)
        data_types = defaultdict(int)
        
        for row in rows:
            if 'status' in row:
                statuses[row['status']] += 1
            if 'significance' in row:
                significance_counts[row['significance']] += 1
            if 'data_type' in row:
                data_types[row['data_type']] += 1
        
        return {
            "total_rows": len(rows),
            "by_status": dict(statuses),
            "by_significance": dict(significance_counts),
            "by_data_type": dict(data_types),
            "critical_changes": sum(1 for r in rows if r.get('significance') == 'high' and r.get('status') != 'NO CHANGE')
        }
    except Exception:
        return {"total_rows": len(rows), "error": "Summary generation failed"}

@app.route("/")
def home():
    return jsonify({"status": "API running - Ultimate OCR v3"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        lines_a = extract_enhanced_text(f1)
        lines_b = extract_enhanced_text(f2)
        
        if not lines_a or not lines_b:
            return jsonify({"error": "Could not extract text from PDFs. Ensure they contain readable text."}), 400
        
        comparison_rows = SmartComparator.smart_compare_lines(lines_a, lines_b)
        summary = generate_summary(comparison_rows)
        
        return jsonify({
            "report": {
                "document_type": "pdf_comparison",
                "purpose": "Intelligent section-based comparison with data typing",
                "comparison_table": comparison_rows,
                "summary": summary
            }
        })
    
    except Exception as e:
        return jsonify({"error": f"Comparison error: {str(e)}"}), 500

@app.route("/summary", methods=["POST"])
def summary():
    try:
        if not OPENAI_API_KEY:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        lines_a = extract_enhanced_text(f1)
        lines_b = extract_enhanced_text(f2)
        
        if not lines_a or not lines_b:
            return jsonify({"error": "Could not extract text from PDFs"}), 400
        
        comparison_rows = SmartComparator.smart_compare_lines(lines_a, lines_b)
        summary_data = generate_summary(comparison_rows)
        
        # Build intelligent summary for AI
        critical_changes = [r for r in comparison_rows if r.get('significance') == 'high' and r.get('status') != 'NO CHANGE']
        
        changes_text = "CRITICAL CHANGES:\n"
        for change in critical_changes[:15]:
            try:
                changes_text += f"- [{change.get('data_type', 'UNKNOWN')}] {change.get('comments', 'Change detected')}\n"
            except Exception:
                continue
        
        qc_prompt = f"""You are a Food Packaging QC Expert reviewing document changes.

CRITICAL CHANGES FOUND:
{changes_text if changes_text != "CRITICAL CHANGES:\n" else "No critical changes detected"}

SUMMARY STATISTICS:
- Total items reviewed: {summary_data.get('total_rows', 0)}
- Critical changes: {summary_data.get('critical_changes', 0)}

TASK: Create a focused, professional QC summary for team review.

Include:
1. Executive summary of critical changes
2. Checkboxes for each item to verify
3. Any compliance or safety concerns
4. Action items

Format for printing and team verification."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": qc_prompt}],
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
        return jsonify({"error": f"Summary error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
