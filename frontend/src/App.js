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
        <h1>📦 Packaging PDF Audit Tool</h1>
        <p>Professional side-by-side comparison with difference highlighting</p>
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
            {loading ? "⏳ Comparing..." : "🔍 Compare Documents"}
          </button>
          <button
            className="btn btn-summary"
            onClick={handleGenerateSummary}
            disabled={!file1 || !file2 || loading}
          >
            {loading ? "⏳ Generating..." : "📋 Generate QC Report"}
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
            <h2>📊 Comparison Results</h2>
            <div className="summary-stats">
              <div className="stat">
                <span className="stat-label">Total Items</span>
                <span className="stat-value">{report.summary.total_rows}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Identical</span>
                <span className="stat-value stat-identical">{report.summary.identical}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Modified</span>
                <span className="stat-value stat-modified">{report.summary.modified}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Added</span>
                <span className="stat-value stat-added">{report.summary.added}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Deleted</span>
                <span className="stat-value stat-deleted">{report.summary.deleted}</span>
              </div>
            </div>
          </div>

          <div className="table-wrapper">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th className="col-element">Element</th>
                  <th className="col-v1">Version A (Original)</th>
                  <th className="col-v2">Version B (Updated) ← Changes Highlighted</th>
                  <th className="col-impact">Status & Impact</th>
                </tr>
              </thead>
              <tbody>
                {report.comparison_table.map((row) => (
                  <tr key={row.row_id} className={`row-${row.status.toLowerCase()}`}>
                    <td className="col-element">
                      <span className="element-tag">{row.element}</span>
                    </td>
                    <td className="col-v1">
                      <div className="content-text">{row.pdf_a_content}</div>
                    </td>
                    <td className="col-v2">
                      <div
                        className="content-text highlighted"
                        dangerouslySetInnerHTML={{
                          __html: row.pdf_b_highlighted,
                        }}
                      />
                    </td>
                    <td className="col-impact">
                      <div className="impact-container">
                        <StatusBadge status={row.status} similarity={row.similarity} />
                        <div className="impact-text">{row.impact}</div>
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
          <h2>📋 AI QC Report</h2>
          <div className="summary-content">
            <pre>{summary}</pre>
          </div>
        </section>
      )}
    </div>
  );
}

function StatusBadge({ status, similarity }) {
  let className = "status-badge";
  let text = status;
  let icon = "";

  if (status === "IDENTICAL") {
    className += " badge-identical";
    icon = "✓";
  } else if (status === "MINOR_CHANGE") {
    className += " badge-minor";
    icon = "⚠️";
  } else if (status === "SIGNIFICANT_CHANGE") {
    className += " badge-significant";
    icon = "🔴";
  } else if (status === "ADDED") {
    className += " badge-added";
    icon = "✨";
  } else if (status === "DELETED") {
    className += " badge-deleted";
    icon = "❌";
  }

  return (
    <span className={className}>
      {icon} {text}
      {similarity !== undefined && similarity !== null && <span> {similarity}%</span>}
    </span>
  );
}
