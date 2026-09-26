export type UiError = {
  title: string;
  message: string;
  nextStep: string;
  status?: number;
};

const httpErrors: Record<number, UiError> = {
  401: { title: "Sign in required", message: "Your session has ended, or the email and password did not match.", nextStep: "Sign in again and retry your request." },
  403: { title: "Request blocked", message: "The server could not verify this request from your browser.", nextStep: "Refresh this page, then try again. If it continues, contact the administrator." },
  404: { title: "Item unavailable", message: "This conversation or PDF could not be found in your workspace.", nextStep: "Return to your list and choose an available item." },
  409: { title: "Action already in progress", message: "The requested change conflicts with the current state of this item.", nextStep: "Wait a moment, refresh the item, and try again." },
  413: { title: "PDF is too large", message: "This file is over the 20 MiB upload limit.", nextStep: "Choose a smaller PDF and upload it again." },
  415: { title: "PDF format needed", message: "The selected file is not a readable PDF upload.", nextStep: "Choose a valid PDF file and try again." },
  422: { title: "Check your entry", message: "Some information is missing or cannot be accepted.", nextStep: "Review the form and try again." },
  429: { title: "Chat is busy", message: "The server is handling as many answers as it can right now.", nextStep: "Wait a few seconds, then retry your message." },
  502: { title: "Server connection unavailable", message: "The application gateway could not reach the API.", nextStep: "Check the server connection, then try again." },
  504: { title: "Server took too long", message: "The application gateway waited too long for the API.", nextStep: "Try again after the server recovers." },
  503: { title: "Service unavailable", message: "A service needed by the application is not ready.", nextStep: "Check server readiness and try again when it recovers." },
};

export async function requestError(response: Response): Promise<UiError> {
  let detail = "";
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch { /* A proxy may return HTML instead of JSON. */ }
  const base = httpErrors[response.status] ?? {
    title: "Request could not be completed",
    message: "The server could not finish this request.",
    nextStep: "Try again. If it continues, contact the administrator.",
  };
  if (response.status === 401 && detail === "Invalid email or password") {
    return { ...base, message: "The email or password did not match.", nextStep: "Check your details and sign in again.", status: 401 };
  }
  return { ...base, status: response.status };
}

export function friendlyError(reason: unknown): UiError {
  if (reason && typeof reason === "object" && "title" in reason && "nextStep" in reason) return reason as UiError;
  return {
    title: "Connection lost",
    message: "The browser could not reach the server or the response stopped early.",
    nextStep: "Check your connection and retry. Your saved work remains in your workspace.",
  };
}

export function chatFailure(code: string | null): UiError {
  const messages: Record<string, string> = {
    model_missing: "The chat model is not installed yet.",
    embedding_model_missing: "The document search model is not installed yet.",
    vector_store_unavailable: "Document search is unavailable.",
    invalid_citation: "The answer included a source that could not be verified.",
    source_unavailable: "A source changed while the answer was being written.",
    ollama_unavailable: "The model service is unavailable.",
    generation_timeout: "The model took too long to answer.",
    queue_timeout: "The request waited too long in the queue.",
    client_disconnected: "The connection closed before the answer finished.",
    interrupted: "The server stopped before the answer finished.",
  };
  return {
    title: "Answer could not finish",
    message: messages[code ?? ""] ?? "The reply could not be completed.",
    nextStep: "Use Retry on this message. Your question is saved.",
  };
}

export function documentFailure(code: string, deleting = false): UiError {
  const messages: Record<string, string> = {
    image_only_pdf: "No searchable text was found in this PDF.",
    encrypted_pdf: "This PDF is encrypted.",
    corrupt_pdf: "This PDF could not be read.",
    too_many_pages: "This PDF has too many pages.",
    too_much_text: "This PDF has too much text.",
    embedding_model_missing: "The document search model is not installed.",
    embedding_service_unavailable: "The embedding service is unavailable.",
    vector_store_unavailable: "The vector database is unavailable.",
    file_missing: "The stored PDF is missing.",
  };
  return {
    title: "PDF needs attention",
    message: messages[code] ?? "This PDF could not be processed.",
    nextStep: deleting
      ? "Retry deletion after the service recovers."
      : code === "image_only_pdf"
        ? "Upload a text PDF. Scanned pages need OCR."
        : ["encrypted_pdf", "corrupt_pdf", "too_many_pages", "too_much_text"].includes(code)
          ? "Prepare a readable, smaller PDF and upload it again."
          : "Retry indexing after the service recovers.",
  };
}

export default function ErrorNotice({ error, onDismiss }: { error: UiError; onDismiss?: () => void }) {
  return (
    <section className="error-notice" role="alert" aria-live="assertive">
      <div className="error-notice-heading">
        <span className="error-badge">{error.status ? `HTTP ${error.status}` : "Needs attention"}</span>
        {onDismiss ? <button className="text-button" type="button" onClick={onDismiss} aria-label="Dismiss error">Dismiss</button> : null}
      </div>
      <h3>{error.title}</h3>
      <p>{error.message}</p>
      <p className="error-next-step">{error.nextStep}</p>
    </section>
  );
}
