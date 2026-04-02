import { useState } from "react";
import axios from "axios";
import "./index.css";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:5000";

export default function App() {
  const [file1, setFile1] = useState(null);
  const [file2, setFile2] = useState(null);
  const [report, setReport] = useState(null);
  const [summary, setSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("comparison");

  const compare = async () => {
    if (!file1 || !file2) {
      setError("Please upload both PDFs");
      return;
    }

    setLoading(true);
    setError("");
    setReport(null);
    setSummary("");

    const formData = new FormData();
    formData.append("file1", file1);
    formData.append("file2", file2);

    try {
      const response = await axios.post(`${API_URL}/compare`, formData);
      setReport(response.data.report);
      setActiveTab("comparison");
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
      setActiveTab("summary");
    } catch (err) {
      setError("Summary failed: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "NO CHANGE":
        return "status-no-change";
      case "MODIFIED":
        return "status-modified";
      case "ADDED":
        return "status-added";
      case "DELETED":
        return "status-deleted";
      default:
        return "";
    }
  };

  return (
    <div className="app-container">
      {/* Header */}
      <div className="header">
        <h1>PDF Compare AI</h1>
        <p className="subtitle">Professional Document Comparison Report</p>
      </div>

      {/* Upload Section */}
      <div className="upload-section">
        <div className="upload-row">
          <div className="upload-group">
            <label htmlFor="file1">Upload PDF #1</label>
            <input
              id="file1"
              type="file"
              accept=".pdf"
              onChange={(e) => setFile1(e.target.files[0])}
            />
            {file1 && <span className="file-name">{file1.name}</span>}
          </div>

          <div className="upload-group">
            <label htmlFor="file2">Upload PDF #2</label>
            <input
              id="file2"
              type="file"
              accept=".pdf"
              onChange={(e) => setFile2(e.target.files[0])}
            />
            {file2 && <span className="file-name">{file2.name}</span>}
          </div>
        </div>

        <div className="button-group">
          <button onClick={compare} className="btn btn-primary" disabled={loading}>
            Compare Documents
          </button>
          <button onClick={generateSummary} className="btn btn-secondary" disabled={loading}>
            Generate AI QC Summary
          </button>
        </div>
      </div>

      {/* Error Message */}
      {error && <div className="error-message">{error}</div>}

      {/* Loading */}
      {loading && <div className="loading">Processing your documents...</div>}

      {/* Tabs */}
      {(report || summary) && (
        <div className="tabs">
          {report && (
            <button
              className={`tab ${activeTab === "comparison" ? "active" : ""}`}
              onClick={() => setActiveTab("comparison")}
            >
              Comparison Table
            </button>
          )}
          {summary && (
            <button
              className={`tab ${activeTab === "summary" ? "active" : ""}`}
              onClick={() => setActiveTab("summary")}
            >
              AI QC Summary
            </button>
          )}
        </div>
      )}

      {/* Comparison Report */}
      {report && activeTab === "comparison" && (
        <div className="report-section">
          {/* Summary Statistics - Hidden */}
          <div className="summary-stats">
            <h2>Report Summary</h2>
            <div className="stats-grid">
              <div className="stat-card">
                <span className="stat-label">Total Rows</span>
                <span className="stat-value">{report.summary.total_rows}</span>
              </div>
              <div className="stat-card status-no-change">
                <span className="stat-label">No Change</span>
                <span className="stat-value">{report.summary.no_change}</span>
              </div>
              <div className="stat-card status-modified">
                <span className="stat-label">Modified</span>
                <span className="stat-value">{report.summary.modified}</span>
              </div>
              <div className="stat-card status-added">
                <span className="stat-label">Added</span>
                <span className="stat-value">{report.summary.added}</span>
              </div>
              <div className="stat-card status-deleted">
                <span className="stat-label">Deleted</span>
                <span className="stat-value">{report.summary.deleted}</span>
              </div>
            </div>
          </div>

          {/* Comparison Table */}
          <div className="table-container">
            <div className="table-wrapper">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Location</th>
                    <th>PDF #1</th>
                    <th>PDF #2</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {report.comparison_table.map((row, index) => (
                    <tr key={index} className={`row-${row.status.toLowerCase().replace(" ", "-")}`}>
                      <td className="cell-row-id">{row.row_id}</td>
                      <td className="cell-tag">{row.tag}</td>
                      <td className="cell-content">
                        {row.pdf_a_content || <span className="empty">—</span>}
                      </td>
                      <td className="cell-content">
                        {row.pdf_b_content ? (
                          <span dangerouslySetInnerHTML={{
                            __html: row.pdf_b_content.replace(/\*\*(.*?)\*\*/g, '<mark>$1</mark>')
                          }} />
                        ) : (
                          <span className="empty">—</span>
                        )}
                      </td>
                      <td className={`cell-status ${getStatusColor(row.status)}`}>
                        {row.status}
                      </td>
                      <td className="cell-comments">Check</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-footer">
              {report.summary.total_rows} total items compared
            </div>
          </div>
        </div>
      )}

      {/* AI QC Summary */}
      {summary && activeTab === "summary" && (
        <div className="summary-section">
          <h2>AI QC Summary</h2>
          <div className="summary-content">
            {summary}
          </div>
        </div>
      )}
    </div>
  );
}
