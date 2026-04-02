from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import difflib
import os
import requests

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
        raise Exception(f"Failed to extract text from PDF: {str(e)}")

@app.route("/")
def home():
    return jsonify({"status": "API running"})

@app.route("/compare", methods=["POST"])
def compare():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both file1 and file2 required"}), 400
        
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
    try:
        if not OPENAI_API_KEY:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({"error": "Both file1 and file2 required"}), 400
        
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        text1 = extract_text(f1)
        text2 = extract_text(f2)
        
        # Call OpenAI API directly via HTTP
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "user",
                    "content": f"Summarize the key differences between these documents:\n\nDoc1:\n{text1[:1000]}\n\nDoc2:\n{text2[:1000]}"
                }
            ]
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({"error": f"OpenAI API error: {response.text}"}), 500
        
        result = response.json()
        summary_text = result["choices"][0]["message"]["content"]
        
        return jsonify({"summary": summary_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
```

**Also update `backend/requirements.txt`:**
```
flask==3.0.0
flask-cors==4.0.0
pdfplumber==0.11.0
gunicorn==21.2.0
requests==2.31.0
