import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const initialProgress = {
  question: { total: 0, ready: 0, active: null },
  answer: { total: 0, ready: 0, active: null },
};

function FilePicker({ label, file, onChange }) {
  return (
    <label className="file-picker">
      <span className="file-label">{label}</span>
      <span className="file-name">{file ? file.name : "Choose a PDF"}</span>
      <span className="file-meta">
        {file
          ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
          : "Drag and drop or browse"}
      </span>
      <input
        type="file"
        accept="application/pdf"
        onChange={(event) => onChange(event.target.files[0] || null)}
      />
    </label>
  );
}

function MappingResults({ markdown }) {
  function downloadMapping() {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "qa-mapping.md";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="mapping-panel">
      <div className="mapping-header">
        <div>
          <h2>Questions and matched answers</h2>
        </div>
        <div className="mapping-actions">
          <button className="download-button" onClick={downloadMapping}>
            Download .md
          </button>
        </div>
      </div>

      <pre className="mapping-content">{markdown}</pre>
    </section>
  );
}

function App() {
  const [questionFile, setQuestionFile] = useState(null);
  const [answerFile, setAnswerFile] = useState(null);
  const [status, setStatus] = useState("Add both PDFs to begin.");
  const [progress, setProgress] = useState(initialProgress);
  const [pages, setPages] = useState({ question: [], answer: [] });
  const [matched, setMatched] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const eventSource = useRef(null);

  useEffect(() => () => eventSource.current?.close(), []);

  function updateProgress(document, update) {
    setProgress((current) => ({
      ...current,
      [document]: { ...current[document], ...update },
    }));
  }

  async function startMapping() {
    if (!questionFile || !answerFile) return;
    eventSource.current?.close();
    setRunning(true);
    setError("");
    setMatched("");
    setPages({ question: [], answer: [] });
    setProgress(initialProgress);
    setStatus("Uploading question paper and answer booklet…");

    const form = new FormData();
    form.append("question_file", questionFile);
    form.append("answer_file", answerFile);

    try {
      const response = await fetch(`${API_URL}/api/jobs`, {
        method: "POST",
        body: form,
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(body.detail || "Could not start QA mapping.");

      const source = new EventSource(
        `${API_URL}/api/jobs/${body.job_id}/events`,
      );
      eventSource.current = source;
      source.addEventListener("status", (event) =>
        setStatus(JSON.parse(event.data).message),
      );
      source.addEventListener("page_count", (event) => {
        const data = JSON.parse(event.data);
        updateProgress(data.document, { total: data.total });
      });
      source.addEventListener("page_started", (event) => {
        const data = JSON.parse(event.data);
        updateProgress(data.document, { active: data.page });
        setStatus(
          `Transcribing ${data.document} page ${data.page} of ${data.total}…`,
        );
      });
      source.addEventListener("page_result", (event) => {
        const result = JSON.parse(event.data);
        setPages((current) => ({
          ...current,
          [result.document]: [...current[result.document], result],
        }));
        updateProgress(result.document, {
          ready: result.page,
          active: null,
        });
      });
      source.addEventListener("matched", (event) => {
        const result = JSON.parse(event.data);
        setMatched(result.markdown);
        setStatus("Questions and answers mapped.");
      });
      source.addEventListener("complete", () => {
        setStatus("Mapping complete.");
        setRunning(false);
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

  const hasPages = pages.question.length > 0 || pages.answer.length > 0;

  return (
    <main className="shell">
      <section className="controls">
        <div className="file-grid">
          <FilePicker
            label="1 · Question paper"
            file={questionFile}
            onChange={setQuestionFile}
          />
          <FilePicker
            label="2 · Answer booklet"
            file={answerFile}
            onChange={setAnswerFile}
          />
        </div>
        <button
          className="primary"
          disabled={!questionFile || !answerFile || running}
          onClick={startMapping}
        >
          {running ? "Mapping…" : "Start QA mapping"}
        </button>
      </section>

      <section className="status-card">
        <div className="status-line">
          <span className={`pulse ${running ? "active" : ""}`} />
          {status}
        </div>
        <div className="document-progress">
          {["question", "answer"].map((document) => {
            const item = progress[document];
            const percentage = item.total
              ? (item.ready / item.total) * 100
              : 0;
            return (
              <div className="progress-item" key={document}>
                <div className="progress-heading">
                  <span>{document === "question" ? "Questions" : "Answers"}</span>
                  <span>
                    {item.ready}/{item.total || "–"}
                    {item.active ? ` · page ${item.active}` : ""}
                  </span>
                </div>
                <div className="progress">
                  <span style={{ width: `${percentage}%` }} />
                </div>
              </div>
            );
          })}
        </div>
        {error && <div className="error">{error}</div>}
      </section>

      {matched && <MappingResults markdown={matched} />}

      {hasPages && (
        <section className="source-section">
          <div className="source-sections">
            {["question", "answer"].map((document) => (
              <div className="source-section-group" key={document}>
                <h3>
                  {document === "question"
                    ? "1. Question paper"
                    : "2. Answer booklet"}
                </h3>
                <div className="source-pages">
                  {pages[document].map((page) => (
                    <article
                      className="page-card"
                      key={`${document}-${page.page}`}
                    >
                      <div className="page-image">
                        <img
                          src={page.image}
                          alt={`${document} page ${page.page}`}
                        />
                      </div>
                      <div className="transcription">
                        <div className="card-heading">
                          Page {page.page}
                          <span className="ready">OCR READY</span>
                        </div>
                        <pre>{page.transcription}</pre>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {!matched && !hasPages && (
        <div className="empty">
          <strong>Your mapping will appear here.</strong>
          <span>Question order, answer status, and review flags will be shown after processing.</span>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
