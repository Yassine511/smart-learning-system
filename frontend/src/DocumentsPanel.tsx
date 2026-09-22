import { FormEvent, useCallback, useEffect, useState } from "react";

type Document = {
  id: string;
  original_filename: string;
  display_name: string;
  status: "pending" | "indexing" | "indexed" | "failed" | "deleting";
  error_code: string | null;
  page_count: number | null;
  size_bytes: number;
};

const errors: Record<string, string> = {
  image_only_pdf: "No searchable text was found. Scanned PDFs need OCR.",
  encrypted_pdf: "This PDF is encrypted.",
  corrupt_pdf: "This PDF could not be read.",
  too_many_pages: "This PDF has too many pages.",
  too_much_text: "This PDF has too much text.",
  embedding_model_missing: "Install the embedding model, then retry indexing.",
  embedding_service_unavailable: "The embedding service is unavailable.",
  vector_store_unavailable: "The vector database is unavailable.",
  file_missing: "The stored PDF is missing.",
};

function csrfToken(): string {
  const cookie = document.cookie.split("; ").find((item) => item.startsWith("smart_learning_csrf="));
  return cookie ? decodeURIComponent(cookie.split("=", 2)[1]) : "";
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? "Request failed (" + response.status + ")";
  } catch {
    return "Request failed (" + response.status + ")";
  }
}

export default function DocumentsPanel({
  conversationId,
  onError,
  onDocumentDeleted,
}: {
  conversationId: string | null;
  onError: (message: string | null) => void;
  onDocumentDeleted: () => void;
}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [attached, setAttached] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const loadDocuments = useCallback(async () => {
    const response = await fetch("/api/documents");
    if (!response.ok) throw new Error(await detail(response));
    setDocuments((await response.json()) as Document[]);
  }, []);

  const loadAttached = useCallback(async (id: string) => {
    const response = await fetch("/api/conversations/" + id + "/documents");
    if (!response.ok) throw new Error(await detail(response));
    setAttached(((await response.json()) as Document[]).map((item) => item.id));
  }, []);

  useEffect(() => {
    void loadDocuments().catch((reason: unknown) => {
      onError(reason instanceof Error ? reason.message : "Could not load PDFs");
    });
  }, [loadDocuments, onError]);

  useEffect(() => {
    if (!conversationId) { setAttached([]); return; }
    void loadAttached(conversationId).catch((reason: unknown) => {
      onError(reason instanceof Error ? reason.message : "Could not load attachments");
    });
  }, [conversationId, loadAttached, onError]);

  useEffect(() => {
    if (!documents.some((item) => ["pending", "indexing", "deleting"].includes(item.status))) return;
    const timer = window.setInterval(() => { void loadDocuments().catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [documents, loadDocuments]);

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
      if (!response.ok) throw new Error(await detail(response));
      form.reset();
      await loadDocuments();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Upload failed");
    } finally { setBusy(false); }
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
      if (!response.ok) throw new Error(await detail(response));
      await loadAttached(conversationId);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not update attachment");
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
      if (!response.ok) throw new Error(await detail(response));
      await loadDocuments();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not rename PDF");
    } finally { setBusy(false); }
  };

  const retry = async (document: Document) => {
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents/" + document.id + "/retry", {
        method: "POST", headers: { "X-CSRF-Token": csrfToken() },
      });
      if (!response.ok) throw new Error(await detail(response));
      await loadDocuments();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not restart indexing");
    } finally { setBusy(false); }
  };

  const remove = async (document: Document) => {
    if (!window.confirm("Delete " + document.display_name + "?")) return;
    setBusy(true); onError(null);
    try {
      const response = await fetch("/api/documents/" + document.id, {
        method: "DELETE", headers: { "X-CSRF-Token": csrfToken() },
      });
      if (!response.ok) throw new Error(await detail(response));
      await loadDocuments();
      if (conversationId) await loadAttached(conversationId);
      onDocumentDeleted();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not delete PDF");
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
      <div className="document-list">
        {documents.map((document) => (
          <article key={document.id}>
            <strong>{document.display_name}</strong>
            <small>{document.status}{document.page_count ? " / " + document.page_count + " pages" : ""}</small>
            {document.error_code ? <p className="error">{errors[document.error_code] ?? document.error_code}</p> : null}
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
