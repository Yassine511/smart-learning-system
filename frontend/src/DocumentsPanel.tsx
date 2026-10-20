import { FormEvent, useCallback, useEffect, useState } from "react";
import { csrfToken } from "./csrf";
import ErrorNotice, { documentFailure, friendlyError, requestError, type UiError } from "./ErrorNotice";
import SourceList, { type Source } from "./SourceList";

type Document = {
  id: string;
  original_filename: string;
  display_name: string;
  status: "pending" | "indexing" | "indexed" | "failed" | "deleting";
  error_code: string | null;
  page_count: number | null;
  size_bytes: number;
};

export default function DocumentsPanel({
  conversationId,
  onError,
  onDocumentDeleted,
}: {
  conversationId: string | null;
  onError: (message: UiError | null) => void;
  onDocumentDeleted: () => void;
}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [attached, setAttached] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Source[] | null>(null);

  const loadDocuments = useCallback(async () => {
    const response = await fetch("/api/documents");
    if (!response.ok) throw await requestError(response);
    setDocuments((await response.json()) as Document[]);
  }, []);

  const loadAttached = useCallback(async (id: string) => {
    const response = await fetch("/api/conversations/" + id + "/documents");
    if (!response.ok) throw await requestError(response);
    setAttached(((await response.json()) as Document[]).map((item) => item.id));
  }, []);

  useEffect(() => {
    void loadDocuments().catch((reason: unknown) => {
      onError(friendlyError(reason));
    });
  }, [loadDocuments, onError]);

  useEffect(() => {
    if (!conversationId) { setAttached([]); return; }
    void loadAttached(conversationId).catch((reason: unknown) => {
      onError(friendlyError(reason));
    });
  }, [conversationId, loadAttached, onError]);

  // Depend on whether any document is still changing, not on the list itself,
  // so each poll's new list does not tear down and recreate the timer.
  const polling = documents.some((item) => ["pending", "indexing", "deleting"].includes(item.status));
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => {
      void loadDocuments().catch((reason: unknown) => {
        // Background polling stays quiet except when the session has ended.
        const failure = friendlyError(reason);
        if (failure.status === 401) onError(failure);
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [polling, loadDocuments, onError]);

  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
        body: new FormData(form),
      });
      if (!response.ok) throw await requestError(response);
      form.reset();
      await loadDocuments();
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); }
  };

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = query.trim();
    if (!text || busy) return;
    setBusy(true); setSearching(true); setResults(null); onError(null);
    try {
      const response = await fetch("/api/documents/search", {
        method: "QUERY",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: text, limit: 5 }),
      });
      if (!response.ok) throw await requestError(response);
      const hits = await response.json() as Omit<Source, "unavailable">[];
      setResults(hits.map((hit) => ({ ...hit, unavailable: false })));
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); setSearching(false); }
  };

  const attach = async (document: Document) => {
    if (!conversationId) return;
    setBusy(true); onError(null);
    try {
      const isAttached = attached.includes(document.id);
      const response = await fetch(
        "/api/conversations/" + conversationId + "/documents/" + document.id,
        { method: isAttached ? "DELETE" : "PUT", headers: { "X-CSRF-Token": csrfToken() } },
      );
      if (!response.ok) throw await requestError(response);
      await loadAttached(conversationId);
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); }
  };

  const rename = async (document: Document) => {
    const name = window.prompt("Document name", document.display_name)?.trim();
    if (!name || name === document.display_name) return;
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents/" + document.id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ display_name: name }),
      });
      if (!response.ok) throw await requestError(response);
      await loadDocuments();
      setResults(null);
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); }
  };

  const retry = async (document: Document) => {
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents/" + document.id + "/retry", {
        method: "POST", headers: { "X-CSRF-Token": csrfToken() },
      });
      if (!response.ok) throw await requestError(response);
      await loadDocuments();
      setResults(null);
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); }
  };

  const remove = async (document: Document) => {
    if (!window.confirm("Delete " + document.display_name + "?")) return;
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents/" + document.id, {
        method: "DELETE", headers: { "X-CSRF-Token": csrfToken() },
      });
      if (!response.ok) throw await requestError(response);
      await loadDocuments();
      setResults(null);
      if (conversationId) await loadAttached(conversationId);
      onDocumentDeleted();
    } catch (reason) {
      onError(friendlyError(reason));
    } finally { setBusy(false); }
  };

  return (
    <section className="status-panel document-panel">
      <p className="section-label">Your PDFs</p>
      <h2>{documents.length} documents</h2>
      <form className="upload-form" onSubmit={(event) => void upload(event)}>
        <label htmlFor="pdf-upload">Upload PDF (up to 20 MiB)</label>
        <input id="pdf-upload" name="file" type="file" accept=".pdf,application/pdf" required />
        <button type="submit" disabled={busy}>Upload PDF</button>
      </form>
      <form className="document-search-form" onSubmit={(event) => void search(event)}>
        <label htmlFor="pdf-search">Search my PDFs</label>
        <input id="pdf-search" type="search" value={query} maxLength={2000} required
          placeholder="Find a passage in your indexed PDFs"
          disabled={searching}
          onChange={(event) => { setQuery(event.target.value); setResults(null); }} />
        <button type="submit" disabled={busy || !query.trim()}>{searching ? "Searching…" : "Search PDFs"}</button>
      </form>
      <div className="document-search-results" role="status" aria-live="polite" aria-busy={searching}>
        {searching ? <p>Searching your indexed PDFs…</p> : null}
        {results !== null ? (
          <>
            <p>{results.length ? `${results.length} matching passages` : "No matching passages in your indexed PDFs."}</p>
            <SourceList sources={results} />
          </>
        ) : null}
      </div>
      <div className="document-list">
        {documents.map((document) => (
          <article key={document.id}>
            <strong>{document.display_name}</strong>
            <small>{document.status}{document.page_count ? " / " + document.page_count + " pages" : ""}</small>
            {document.error_code ? <ErrorNotice error={documentFailure(document.error_code, document.status === "deleting")} /> : null}
            <div className="document-actions">
              {document.status !== "deleting" ? (
                <a href={"/api/documents/" + document.id + "/file"} target="_blank" rel="noreferrer">View PDF</a>
              ) : null}
              {conversationId && (document.status === "indexed" || attached.includes(document.id)) ? (
                <button type="button" className="text-button" onClick={() => void attach(document)} disabled={busy}>
                  {attached.includes(document.id) ? "Detach" : "Attach"}
                </button>
              ) : null}
              <button type="button" className="text-button" onClick={() => void rename(document)}
                disabled={busy || document.status === "deleting"}>Rename</button>
              {["failed", "indexed"].includes(document.status) ? (
                <button type="button" className="text-button" onClick={() => void retry(document)}
                  disabled={busy}>Reindex</button>
              ) : null}
              <button type="button" className="text-button" onClick={() => void remove(document)}
                disabled={busy || (document.status === "deleting" && !document.error_code)}>
                {document.status === "deleting" ? "Retry deletion" : "Delete"}
              </button>
            </div>
          </article>
        ))}
        {documents.length === 0 ? <p>No PDFs uploaded yet.</p> : null}
      </div>
    </section>
  );
}
