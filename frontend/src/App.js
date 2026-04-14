import React, { useState } from "react";
import axios from "axios";
import "./ComparisonTable.css";

const API_BASE_URL = process.env.REACT_APP_API_URL || "https://pdf-compare-ai-api.onrender.com";

export default function App() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [report, setReport] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedRows, setExpandedRows] = useState(new Set());

  const handleFileChange = (e, fileNumber) => {
    const file = e.target.files[0];
    if (fileNumber === 1) {
      setFile1(file);
    } else {
      setFile2(file);
    }
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

  const toggleRowExpanded = (rowId) => {
    const newExpanded = new Set(expandedRows);
    if (newExpanded.has(rowId)) {
      newExpanded.delete(rowId);
    } else {
      newExpanded.add(rowId);
    }
    setExpandedRows(newExpanded);
  };

  return (
    <div className="app-container">
      <header className="app-header">
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
                  <th className="col-v2">Version B (Updated)</th>
                  <th className="col-status">Status</th>
                  <th className="col-impact">Impact</th>
                </tr>
              </thead>
              <tbody>
                {report.comparison_table.map((row) => {
                  const isExpanded = expandedRows.has(row.row_id);
                  const isIdentical = row.status === "IDENTICAL";
                  const isAdded = row.status === "ADDED";
                  const isDeleted = row.status === "DELETED";
                  const isModified = ["MINOR_CHANGE", "SIGNIFICANT_CHANGE"].includes(
                    row.status
                  );

                  return (
                    <React.Fragment key={row.row_id}>
                      <tr
                        className={`row-${row.status.toLowerCase()} ${
                          isExpanded ? "expanded" : ""
                        }`}
                        onClick={() => toggleRowExpanded(row.row_id)}
                      >
                        <td className="col-element">
                          <span className="element-tag">{row.tag}</span>
                        </td>
                        <td className="col-v1">
                          <div className="content-preview">{row.pdf_a_content}</div>
                        </td>
                        <td className="col-v2">
                          <div
                            className="content-preview highlighted"
                            dangerouslySetInnerHTML={{
                              __html: row.pdf_b_highlighted || row.pdf_b_content,
                            }}
                          />
                        </td>
                        <td className="col-status">
                          <StatusBadge status={row.status} similarity={row.similarity} />
                        </td>
                        <td className="col-impact">
                          <span className="impact-text">{row.impact}</span>
                          {row.changes && row.changes.length > 0 && (
                            <span className="expand-icon">▼</span>
                          )}
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr className="expanded-row">
                          <td colSpan="5">
                            <div className="expanded-content">
                              <div className="full-text-section">
                                <div className="full-text-col">
                                  <h4>Original Text</h4>
                                  <div className="full-text">
                                    {row.full_text_a || row.pdf_a_content}
                                  </div>
                                </div>
                                <div className="full-text-col">
                                  <h4>Updated Text</h4>
                                  <div
                                    className="full-text highlighted"
                                    dangerouslySetInnerHTML={{
                                      __html: row.pdf_b_highlighted || row.pdf_b_content,
                                    }}
                                  />
                                </div>
                              </div>

                              {row.changes && row.changes.length > 0 && (
                                <div className="changes-list">
                                  <h4>Detailed Changes</h4>
                                  <ul>
                                    {row.changes.map((change, idx) => (
                                      <li key={idx} className={`change-${change.type.toLowerCase()}`}>
                                        <strong>{change.type}:</strong> {change.content}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
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

  if (status === "IDENTICAL") {
    className += " badge-identical";
  } else if (status === "MINOR_CHANGE") {
    className += " badge-minor";
  } else if (status === "SIGNIFICANT_CHANGE") {
    className += " badge-significant";
  } else if (status === "ADDED") {
    className += " badge-added";
  } else if (status === "DELETED") {
    className += " badge-deleted";
  }

  return (
    <span className={className}>
      {text}
      {similarity !== undefined && similarity !== null && <span> {similarity}%</span>}
    </span>
  );
}
