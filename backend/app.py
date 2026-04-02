from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import requests
import os
import difflib
import re
from typing import List, Dict, Tuple, Set
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
        # Remove extra whitespace but preserve structure
        text = ' '.join(text.split())
        # Normalize common OCR errors
        text = text.replace('l3', '13')  # Common OCR error
        text = text.replace('O0', '00')  # Zero vs O
        text = text.replace('S5', '55')  # S vs 5
        return text.strip()
    
    @staticmethod
    def detect_data_type(text: str) -> str:
        """Detect what type of data this is"""
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
    
    @staticmethod
    def extract_sections(lines: List[str]) -> Dict[str, List[str]]:
        """Intelligently group text into sections"""
        sections = defaultdict(list)
        current_section = 'GENERAL'
        
        for line in lines:
            if not line.strip():
                continue
            
            # Check if this line is a section header
            line_lower = line.lower()
            for section_name, pattern in SECTION_KEYWORDS.items():
                if re.search(pattern, line_lower):
                    current_section = section_name
                    break
            
            sections[current_section].append(line)
        
        return dict(sections)

class SmartComparator:
    """Intelligent comparison engine"""
    
    @staticmethod
    def fuzzy_match(text_a: str, text_b: str, threshold: float = 0.85) -> Tuple[bool, float]:
        """Fuzzy matching for similar text"""
        ratio = difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
        return ratio >= threshold, ratio
    
    @staticmethod
    def is_significant_change(original: str, updated: str, data_type: str) -> bool:
        """Determine if a change is significant"""
        # All allergen changes are critical
        if data_type == 'ALLERGEN':
            return True
        
        # Numbers - flag if change is >5%
        if data_type in ['WEIGHT', 'PERCENTAGE', 'ENERGY']:
            try:
                orig_num = float(re.findall(r'\d+(?:[.,]\d+)?', original)[0].replace(',', '.'))
                upd_num = float(re.findall(r'\d+(?:[.,]\d+)?', updated)[0].replace(',', '.'))
                percent_change = abs(upd_num - orig_num) / orig_num * 100
                return percent_change > 5
            except:
                return True
        
        # Default: all changes are significant
        return True
    
    @staticmethod
    def smart_compare_lines(lines_a: List[str], lines_b: List[str]) -> List[Dict]:
        """Compare with intelligence"""
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
                        rows.append({
                            "row_id": f"R{row_id}",
                            "tag": section,
                            "line": f"Line {i1 + i + 1}",
                            "pdf_a_content": processor.normalize_text(section_lines_a[i1 + i])[:120],
                            "pdf_b_content": processor.normalize_text(section_lines_a[i1 + i])[:120],
                            "data_type": processor.detect_data_type(section_lines_a[i1 + i]),
                            "status": "NO CHANGE",
                            "significance": "low",
                            "comments": "Unchanged"
                        })
                        row_id += 1
                
                elif tag == 'replace':
                    max_lines = max(i2 - i1, j2 - j1)
                    for i in range(max_lines):
                        a_line = section_lines_a[i1 + i] if i1 + i < i2 else ""
                        b_line = section_lines_b[j1 + i] if j1 + i < j2 else ""
                        
                        a_norm = processor.normalize_text(a_line)
                        b_norm = processor.normalize_text(b_line)
                        
                        if a_norm != b_norm:
                            # Try fuzzy match first
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
                
                elif tag == 'delete':
                    for i in range(i2 - i1):
                        line = section_lines_a[i1 + i]
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
                
                elif tag == 'insert':
                    for i in range(j2 - j1):
                        line = section_lines_b[j1 + i]
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
        
        return rows
    
    @staticmethod
    def highlight_differences(text_a: str, text_b: str) -> str:
        """Highlight only changed parts"""
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
    
    @staticmethod
    def generate_smart_comment(text_a: str, text_b: str, data_type: str) -> str:
        """Generate intelligent comments"""
        if data_type == 'PERCENTAGE':
            try:
                a_val = float(re.findall(r'\d+(?:[.,]\d+)?', text_a)[0].replace(',', '.'))
                b_val = float(re.findall(r'\d+(?:[.,]\d+)?', text_b)[0].replace(',', '.'))
                change = b_val - a_val
                return f"{data_type}: {a_val}% to {b_val}% (change: {change:+.1f}%)"
            except:
                return f"{data_type} modified"
        
        elif data_type == 'WEIGHT':
            return f"{data_type}: {text_a} to {text_b}"
        
        elif data_type == 'ALLERGEN':
            return f"⚠ CRITICAL - Allergen info changed"
        
        elif data_type == 'DATE':
            return f"Date: {text_a} to {text_b}"
        
        else:
            return f"{data_type} modified"

def extract_enhanced_text(file) -> List[str]:
    """Extract text with all enhancements"""
    try:
        with pdfplumber.open(file) as pdf:
            all_lines = []
            
            for page in pdf.pages:
                # Try to extract tables first
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            row_text = ' | '.join([str(cell).strip() if cell else '' for cell in row])
                            if row_text.strip():
                                all_lines.append(row_text)
                
                # Then extract regular text with spatial awareness
                words = page.extract_words()
                if words:
                    sorted_words = sorted(words, key=lambda w: (round(w['top'] / 20) * 20, w['left']))
                    
                    current_line = []
                    current_y = None
                    
                    for word in sorted_words:
                        word_y = round(word['top'] / 20) * 20
                        
                        if current_y is not None and word_y != current_y:
                            if current_line:
                                all_lines.append(' '.join(current_line).strip())
                            current_line = []
                        
                        current_line.append(word['text'])
                        current_y = word_y
                    
                    if current_line:
                        all_lines.append(' '.join(current_line).strip())
            
            return [line for line in all_lines if line]
    
    except Exception as e:
        raise Exception(f"Text extraction failed: {str(e)}")

def generate_summary(rows: List[Dict]) -> Dict:
    """Generate comprehensive summary"""
    statuses = defaultdict(int)
    significance_counts = defaultdict(int)
    data_types = defaultdict(int)
    
    for row in rows:
        statuses[row['status']] += 1
        significance_counts[row['significance']] += 1
        data_types[row['data_type']] += 1
    
    return {
        "total_rows": len(rows),
        "by_status": dict(statuses),
        "by_significance": dict(significance_counts),
        "by_data_type": dict(data_types),
        "critical_changes": sum(1 for r in rows if r.get('significance') == 'high' and r['status'] != 'NO CHANGE')
    }

@app.route("/")
def home():
    return jsonify({"status": "API running - Enhanced OCR v2"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both files required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        lines_a = extract_enhanced_text(f1)
        lines_b = extract_enhanced_text(f2)
        
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
        
        lines_a = extract_enhanced_text(f1)
        lines_b = extract_enhanced_text(f2)
        
        comparison_rows = SmartComparator.smart_compare_lines(lines_a, lines_b)
        summary_data = generate_summary(comparison_rows)
        
        # Build intelligent summary for AI
        critical_changes = [r for r in comparison_rows if r.get('significance') == 'high' and r['status'] != 'NO CHANGE']
        medium_changes = [r for r in comparison_rows if r.get('significance') == 'medium' and r['status'] != 'NO CHANGE']
        minor_changes = [r for r in comparison_rows if r['status'] in ['MINOR_VARIATION', 'NO CHANGE']]
        
        changes_text = "CRITICAL CHANGES:\n"
        for change in critical_changes[:10]:
            changes_text += f"- [{change['data_type']}] {change['comments']}\n"
        
        changes_text += "\nMEDIUM CHANGES:\n"
        for change in medium_changes[:10]:
            changes_text += f"- [{change['data_type']}] {change['comments']}\n"
        
        qc_prompt = f"""You are a Food Packaging QC Expert reviewing document changes.

CRITICAL CHANGES (High Priority):
{changes_text}

SUMMARY STATISTICS:
- Total sections reviewed: {len(summary_data.get('by_status', {}))}
- Critical changes found: {summary_data.get('critical_changes', 0)}
- Data types affected: {', '.join(summary_data.get('by_data_type', {}).keys())}

TASK: Create a focused, professional QC summary for immediate review.

Include:
1. Executive summary of critical changes
2. Organized by section (ALLERGENS, NUTRITION, INGREDIENTS, etc.)
3. Checkboxes for each item to verify
4. Any compliance or safety concerns
5. Action items

Be concise, professional, and emphasize critical changes. Format for printing and team review."""

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
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
