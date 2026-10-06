import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function App() {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("Upload a PDF to begin.");
  const [total, setTotal] = useState(null);
  const [activePage, setActivePage] = useState(null);
  const [pages, setPages] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const eventSource = useRef(null);

  useEffect(() => () => eventSource.current?.close(), []);

  async function startTranscription() {
    if (!file) return;
    setRunning(true);
    setError("");
    setPages([]);
    setTotal(null);
    setActivePage(null);
    setStatus("Uploading PDF…");
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await fetch(`${API_URL}/api/jobs`, {
        method: "POST",
        body: form,
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(body.detail || "Could not start transcription.");
      const source = new EventSource(
        `${API_URL}/api/jobs/${body.job_id}/events`,
      );
      eventSource.current = source;
      source.addEventListener("status", (event) =>
        setStatus(JSON.parse(event.data).message),
      );
      source.addEventListener("page_count", (event) => {
        const data = JSON.parse(event.data);
        setTotal(data.total);
        setStatus(
          `Processing ${data.total} page${data.total === 1 ? "" : "s"}…`,
        );
      });
      source.addEventListener("page_started", (event) => {
        const data = JSON.parse(event.data);
        setActivePage(data.page);
        setStatus(`Transcribing page ${data.page} of ${data.total}…`);
      });
      source.addEventListener("page_result", (event) => {
        const result = JSON.parse(event.data);
        setPages((current) => [...current, result]);
        setStatus(`Page ${result.page} of ${result.total} is ready.`);
      });
      source.addEventListener("complete", () => {
        setStatus("All pages transcribed.");
        setRunning(false);
        setActivePage(null);
        source.close();
      });
      source.addEventListener("job_error", (event) => {
        if (event.data) setError(JSON.parse(event.data).message);
        setRunning(false);
        source.close();
      });
    } catch (requestError) {
      setError(requestError.message);
      setRunning(false);
    }
  }

  return (
    <main className="shell">
      <header>
        <h1>Exam script transcription</h1>
      </header>
      <section className="controls">
        <label className="dropzone">
          <span className="drop-title">
            {file ? file.name : "Choose a PDF"}
          </span>
          <span className="drop-subtitle">
            {file
              ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
              : "Drag and drop or browse"}
          </span>
          <input
            type="file"
            accept="application/pdf"
            onChange={(event) => setFile(event.target.files[0])}
          />
        </label>
        <button
          className="primary"
          disabled={!file || running}
          onClick={startTranscription}
        >
          {running ? "Processing…" : "Start"}
        </button>
      </section>
      <section className="status-card">
        <div className="status-line">
          <span className={`pulse ${running ? "active" : ""}`} />
          {status}
        </div>
        {total !== null && (
          <div className="progress">
            <span style={{ width: `${(pages.length / total) * 100}%` }} />
          </div>
        )}
        {total !== null && (
          <div className="progress-label">
            {pages.length} of {total} pages ready
            {activePage ? ` · working on page ${activePage}` : ""}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </section>
      <section className="results">
        {pages.map((page) => (
          <article className="page-card" key={page.page}>
            <div className="page-image">
              <img src={page.image} alt={`Rendered exam page ${page.page}`} />
            </div>
            <div className="transcription">
              <div className="card-heading">
                Page {page.page}
                <span className="ready">READY</span>
              </div>
              <pre>{page.transcription}</pre>
            </div>
          </article>
        ))}
        {!pages.length && (
          <div className="empty">Your transcriptions will appear here.</div>
        )}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
