import { useState } from "react";
import axios from "axios";
import "./index.css";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:5000";

export default function App() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [diffHtml, setDiffHtml] = useState("");
  const [summary, setSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const compare = async () => {
    if (!file1 || !file2) {
      setError("Please upload both PDFs");
      return;
    }

    setLoading(true);
    setError("");

    const formData = new FormData();
    formData.append("file1", file1);
    formData.append("file2", file2);

    try {
      const response = await axios.post(`${API_URL}/compare`, formData);
      setDiffHtml(response.data.html);
    } catch (err) {
      setError("Comparison failed: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  const generateSummary = async () => {
    if (!file1 || !file2) {
      setError("Please upload both PDFs");
      return;
    }

    setLoading(true);
    setError("");

    const formData = new FormData();
    formData.append("file1", file1);
    formData.append("file2", file2);

    try {
      const response = await axios.post(`${API_URL}/summary`, formData);
      setSummary(response.data.summary);
    } catch (err) {
      setError("Summary failed: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <h1>📄 PDF Compare AI</h1>
      
      <div className="upload-section">
        <div>
          <label>Upload PDF #1:</label>
          <input
            type="file"
            accept=".pdf"
            onChange={(e) => setFile1(e.target.files[0])}
          />
        </div>

        <div>
          <label>Upload PDF #2:</label>
          <input
            type="file"
            accept=".pdf"
            onChange={(e) => setFile2(e.target.files[0])}
          />
        </div>

        <button onClick={compare}>🔍 Compare Documents</button>
        <button onClick={generateSummary}>✨ Generate Summary</button>
      </div>

      {error && <div style={{ color: "red", padding: "10px" }}>{error}</div>}

      {loading && <div className="loading">Processing...</div>}

      {summary && (
        <div className="results">
          <h2>Summary</h2>
          <p>{summary}</p>
        </div>
      )}

      {diffHtml && (
        <div className="results">
          <h2>Detailed Comparison</h2>
          <div dangerouslySetInnerHTML={{ __html: diffHtml }} />
        </div>
      )}
    </div>
  );
}
