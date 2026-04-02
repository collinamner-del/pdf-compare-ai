from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import fitz
import difflib
from openai import OpenAI

app = Flask(__name__)
CORS(app)
client = OpenAI()

def extract_text(file):
    pdf = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join([p.get_text() for p in pdf])

@app.route("/")
def home():
    return {"status": "API running"}

@app.route("/compare", methods=["POST"])
def compare():
    try:
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        text1 = extract_text(f1)
        text2 = extract_text(f2)
        
        # Generate diff
        differ = difflib.HtmlDiff()
        html = differ.make_file(text1.splitlines(), text2.splitlines())
        
        return {"html": html}
    except Exception as e:
        return {"error": str(e)}, 400

@app.route("/summary", methods=["POST"])
def summary():
    try:
        f1 = request.files["file1"]
        f2 = request.files["file2"]
        
        text1 = extract_text(f1)
        text2 = extract_text(f2)
        
        # Use OpenAI to summarize differences
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{
                "role": "user",
                "content": f"Summarize the key differences between these documents:\n\nDoc1:\n{text1[:1000]}\n\nDoc2:\n{text2[:1000]}"
            }]
        )
        
        return {"summary": response.choices[0].message.content}
    except Exception as e:
        return {"error": str(e)}, 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
