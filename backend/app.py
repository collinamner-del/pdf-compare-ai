from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import difflib

app = Flask(__name__)
CORS(app)

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
        
        text1 = extract_text(f1)
        text2 = extract_text(f2)
        
        differ = difflib.HtmlDiff()
        html = differ.make_file(text1.splitlines(), text2.splitlines())
        
        return jsonify({"html": html})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/summary", methods=["POST"])
def summary():
    return jsonify({"summary": "Summary feature disabled on free tier. Please upgrade Render for AI features."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
