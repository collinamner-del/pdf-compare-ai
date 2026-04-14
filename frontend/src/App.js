import React, { useState } from "react";
import axios from "axios";
import "./index.css";

const API_BASE_URL = process.env.REACT_APP_API_URL || "https://pdf-compare-ai-api.onrender.com";

export default function App() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [report, setReport] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleFileChange = (e, fileNumber) => {
    const file = e.target.files[0];
    if (fileNumber === 1) setFile1(file);
    else setFile2(file);
  };

  const handleCompare = async () => {
    if (!file1 || !file2) {
      setError("Please select both PDF files");
      return;
    }

    setError(null);
    setLoading(true);
    setReport(null);
    setSummary(null);

    try {
      const formData = new FormData();
      formData.append("file1", file1);
      formData.append("file2", file2);

      const response = await axios.post(`${API_BASE_URL}/compare`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      setReport(response.data.report);
    } catch (err) {
      setError(err.response?.data?.error || "Comparison failed");
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateSummary = async () => {
    if (!file1 || !file2) {
      setError("Please select both PDF files");
      return;
    }

    setError(null);
    setLoading(true);

    try {
      const formData = new FormData();
      formData.append("file1", file1);
      formData.append("file2", file2);

      const response = await axios.post(`${API_BASE_URL}/summary`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      setSummary(response.data.summary);
    } catch (err) {
      setError(err.response?.data?.error || "Summary generation failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>📦 Packaging QC Audit Tool</h1>
        <p>Precise change detection with ACTION-focused reporting</p>
      </header>

      <section className="upload-section">
        <div className="upload-group">
          <label className="upload-label">
            <span>📄 PDF #1 (Original)</span>
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => handleFileChange(e, 1)}
              disabled={loading}
            />
            {file1 && <span className="file-name">{file1.name}</span>}
          </label>
        </div>

        <div className="upload-group">
          <label className="upload-label">
            <span>📄 PDF #2 (Updated)</span>
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => handleFileChange(e, 2)}
              disabled={loading}
            />
            {file2 && <span className="file-name">{file2.name}</span>}
          </label>
        </div>

        <div className="button-group">
          <button
            className="btn btn-compare"
            onClick={handleCompare}
            disabled={!file1 || !file2 || loading}
          >
            {loading ? "⏳ Analyzing..." : "🔍 Compare & Detect Changes"}
          </button>
          <button
            className="btn btn-summary"
            onClick={handleGenerateSummary}
            disabled={!file1 || !file2 || loading}
          >
            {loading ? "⏳ Generating..." : "✅ QC Checklist"}
          </button>
        </div>
      </section>

      {error && (
        <div className="error-section">
          <strong>⚠️ Error:</strong> {error}
        </div>
      )}

      {report && (
        <section className="report-section">
          <div className="report-header">
            <h2>📋 Change Detection Report</h2>
            <div className="summary-stats">
              <div className="stat">
                <span className="stat-label">Total</span>
                <span className="stat-value">{report.summary.total_rows}</span>
              </div>
              <div className="stat">
                <span className="stat-label">✓ No Change</span>
                <span className="stat-value stat-identical">{report.summary.identical}</span>
              </div>
              <div className="stat">
                <span className="stat-label">⚠️ Minor</span>
                <span className="stat-value stat-modified">{report.summary.minor}</span>
              </div>
              <div className="stat">
                <span className="stat-label">🔴 Significant</span>
                <span className="stat-value stat-significant">{report.summary.significant}</span>
              </div>
              <div className="stat">
                <span className="stat-label">✨ Added</span>
                <span className="stat-value stat-added">{report.summary.added}</span>
              </div>
              <div className="stat">
                <span className="stat-label">❌ Deleted</span>
                <span className="stat-value stat-deleted">{report.summary.deleted}</span>
              </div>
            </div>
          </div>

          <div className="table-wrapper">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th className="col-element">Element</th>
                  <th className="col-v1">Original (V1)</th>
                  <th className="col-v2">Updated (V2) ← Changes Highlighted</th>
                  <th className="col-action">ACTION FOR QC</th>
                </tr>
              </thead>
              <tbody>
                {report.comparison_table.map((row) => (
                  <tr key={row.row_id} className={`row-${row.status.toLowerCase()}`}>
                    <td className="col-element">
                      <span className="element-tag">{row.element}</span>
                    </td>
                    <td className="col-v1">
                      <div className="text-content">{row.pdf_a}</div>
                    </td>
                    <td className="col-v2">
                      <div
                        className="text-content highlighted"
                        dangerouslySetInnerHTML={{ __html: row.pdf_b_highlighted }}
                      />
                    </td>
                    <td className="col-action">
                      <div className="action-box">
                        <div className="action-text">{row.action}</div>
                        {row.changes && row.changes.length > 0 && (
                          <div className="changes-list">
                            {row.changes.map((change, idx) => (
                              <div key={idx} className="change-item">
                                {change}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {summary && (
        <section className="summary-section">
          <h2>✅ QC Verification Checklist</h2>
          <div className="summary-content">
            <pre>{summary}</pre>
          </div>
        </section>
      )}
    </div>
  );
}
