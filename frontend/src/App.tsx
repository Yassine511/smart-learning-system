import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import DocumentsPanel from "./DocumentsPanel";
import SourceList, { type Source } from "./SourceList";
import ErrorNotice, { chatFailure, friendlyError, requestError, type UiError } from "./ErrorNotice";
import { csrfToken } from "./csrf";

type ComponentState = { ok: boolean; detail: string };
type Readiness = { status: "ready" | "not_ready"; components: Record<string, ComponentState> };
type User = { id: string; email: string; display_name: string };
type Conversation = { id: string; title: string; created_at: string; updated_at: string };
type Message = {
  id: string;
  request_id: string;
  role: "user" | "assistant";
  status: "queued" | "running" | "completed" | "failed";
  content: string;
  error_code: string | null;
  created_at: string;
  sources: Source[];
};
type LoginResponse = { user: User };
type ChatEvent = { request_id: string; message_id?: string; status?: string; content?: string; error_code?: string; document_id?: string; chunk_id?: string; marker?: string; filename?: string; page_number?: number; quote?: string; unavailable?: boolean };

const labels: Record<string, string> = {
  postgres: "PostgreSQL",
  qdrant: "Qdrant",
  ollama: "Ollama",
};

async function readChatStream(
  response: Response,
  onEvent: (name: string, payload: ChatEvent) => void,
): Promise<void> {
  if (!response.body) throw new Error("The chat stream did not start.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const name = block.split("\n").find((line) => line.startsWith("event: "))?.slice(7);
        const data = block.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
        if (name && data) {
          const payload = JSON.parse(data) as ChatEvent;
          onEvent(name, payload);
          if (name === "complete" || name === "failed") terminal = true;
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (!terminal) throw new Error("The chat connection ended before a final response.");
}

function App() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draft = selected ? drafts[selected.id] ?? "" : "";
  const selectedId = useRef<string | null>(null);
  const messageLoadVersion = useRef(0);
  const pendingSubmissions = useRef(new Map<string, { requestId: string; content: string }>());
  // Drafts survive an expired session only for the student who wrote them.
  const draftOwner = useRef<string | null>(null);
  const setDraft = (value: string) => {
    if (selected) setDrafts((current) => ({ ...current, [selected.id]: value }));
  };
  const [liveAnswer, setLiveAnswer] = useState("");
  const [liveSources, setLiveSources] = useState<Source[]>([]);
  const [chatStatus, setChatStatus] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UiError | null>(null);

  const resetSession = useCallback((keepDrafts: boolean) => {
    setUser(null);
    selectedId.current = null;
    messageLoadVersion.current += 1;
    if (!keepDrafts) {
      pendingSubmissions.current.clear();
      setDrafts({});
      draftOwner.current = null;
    }
    setConversations([]);
    setSelected(null);
    setMessages([]);
    setLiveAnswer("");
    setLiveSources([]);
  }, []);

  // A 401 after sign-in means the server session ended (expired or signed out
  // elsewhere). Return to the sign-in form instead of leaving a dead workspace.
  const reportError = useCallback((next: UiError | null) => {
    if (next?.status === 401) resetSession(true);
    setError(next);
  }, [resetSession]);

  const signedIn = useCallback((next: User) => {
    if (draftOwner.current !== next.id) {
      pendingSubmissions.current.clear();
      setDrafts({});
    }
    draftOwner.current = next.id;
    setUser(next);
  }, []);

  const refreshReadiness = useCallback(async () => {
    try {
      const response = await fetch("/api/ready", { headers: { Accept: "application/json" } });
      setReadiness((await response.json()) as Readiness);
    } catch {
      setReadiness(null);
    }
  }, []);

  const loadConversations = useCallback(async () => {
    const response = await fetch("/api/conversations");
    if (!response.ok) throw await requestError(response);
    setConversations((await response.json()) as Conversation[]);
  }, []);

  const loadMessages = useCallback(async (id: string) => {
    const version = ++messageLoadVersion.current;
    const isCurrent = () => version === messageLoadVersion.current && selectedId.current === id;
    try {
      const response = await fetch(`/api/conversations/${id}/messages`);
      if (!response.ok) throw await requestError(response);
      const rows = (await response.json()) as Message[];
      if (isCurrent()) setMessages(rows);
    } catch (reason) {
      if (isCurrent()) throw reason;
    }
  }, []);

  useEffect(() => {
    const bootstrap = async () => {
      await refreshReadiness();
      const response = await fetch("/api/me");
      if (response.ok) {
        signedIn((await response.json()) as User);
        await loadConversations();
      } else if (response.status !== 401) {
        throw await requestError(response);
      }
      setLoading(false);
    };
    void bootstrap().catch((reason: unknown) => {
      setError(friendlyError(reason));
      setLoading(false);
    });
  }, [loadConversations, refreshReadiness, signedIn]);

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const form = new FormData(event.currentTarget);
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (!response.ok) throw await requestError(response);
      const payload = (await response.json()) as LoginResponse;
      signedIn(payload.user);
      await loadConversations();
    } catch (reason) {
      setError(friendlyError(reason));
    } finally {
      setBusy(false);
    }
  };

  const createConversation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData(formElement);
      const response = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ title: form.get("title") }),
      });
      if (!response.ok) throw await requestError(response);
      const created = (await response.json()) as Conversation;
      formElement.reset();
      await loadConversations();
      selectedId.current = created.id;
      messageLoadVersion.current += 1;
      setSelected(created);
      setMessages([]);
    } catch (reason) {
      reportError(friendlyError(reason));
    } finally {
      setBusy(false);
    }
  };

  const openConversation = async (conversation: Conversation) => {
    selectedId.current = conversation.id;
    messageLoadVersion.current += 1;
    setSelected(conversation);
    setMessages([]);
    setLiveAnswer("");
    setLiveSources([]);
    setChatStatus("");
    setError(null);
    try {
      await loadMessages(conversation.id);
    } catch (reason) {
      reportError(friendlyError(reason));
    }
  };

  const sendChat = async (conversationId: string, requestId: string, content: string) => {
    setChatBusy(true);
    setChatStatus("Connecting");
    setLiveAnswer("");
    setLiveSources([]);
    setError(null);
    try {
      const response = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ request_id: requestId, content }),
      });
      if (!response.ok) throw await requestError(response);
      await readChatStream(response, (name, payload) => {
        if (name === "status") setChatStatus(payload.status ?? "Working");
        if (name === "delta") setLiveAnswer((current) => current + (payload.content ?? ""));
        if (name === "source" && payload.document_id && payload.chunk_id && payload.marker) {
          setLiveSources((current) => [...current, {
            document_id: payload.document_id!, chunk_id: payload.chunk_id!,
            marker: payload.marker!, filename: payload.filename ?? "PDF",
            page_number: payload.page_number ?? 1, quote: payload.quote ?? "",
            unavailable: payload.unavailable ?? false,
          }]);
        }
        if (name === "failed") setError(chatFailure(payload.error_code ?? null));
        if (name === "complete") setChatStatus("Completed");
        if (name === "complete" || name === "failed") {
          // Only a terminal event resolves an uncertain submission. Preserve
          // newer edits and reuse the UUID if the connection fails beforehand.
          setDrafts((current) => current[conversationId]?.trim() === content
            ? { ...current, [conversationId]: "" } : current);
          if (pendingSubmissions.current.get(conversationId)?.requestId === requestId) {
            pendingSubmissions.current.delete(conversationId);
          }
        }
      });
      await loadMessages(conversationId);
      await loadConversations();
    } catch (reason) {
      reportError(friendlyError(reason));
      try { await loadMessages(conversationId); } catch { /* Keep the visible error. */ }
    } finally {
      setLiveAnswer("");
      setLiveSources([]);
      setChatBusy(false);
      setChatStatus("");
    }
  };

  const submitChat = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !draft.trim() || chatBusy) return;
    const content = draft.trim();
    const pending = pendingSubmissions.current.get(selected.id);
    const requestId = pending?.content === content ? pending.requestId : crypto.randomUUID();
    pendingSubmissions.current.set(selected.id, { requestId, content });
    void sendChat(selected.id, requestId, content);
  };

  const retry = (assistant: Message) => {
    if (!selected || chatBusy) return;
    const question = messages.find((message) =>
      message.role === "user" && message.request_id === assistant.request_id
    );
    if (question) void sendChat(selected.id, assistant.request_id, question.content);
  };

  const logout = async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      // 401: the session already ended on the server, so the student is signed out.
      if (!response.ok && response.status !== 401) throw await requestError(response);
      resetSession(false);
    } catch (reason) {
      setError(friendlyError(reason));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <main className="loading-screen">Preparing your private workspace…</main>;

  return (
    <main>
      <header className="topbar">
        <span>Smart Learning System</span>
        {user ? <button className="text-button" type="button" onClick={() => void logout()} disabled={busy || chatBusy}>Log out</button> : null}
      </header>
      {!user ? (
        <section className="login-layout">
          <div className="hero">
            <p className="eyebrow">Private learning · three-VM lab</p>
            <h1>Learn from your own material.</h1>
            <p className="summary">Sign in with the student account created by the administrator. Your conversations and documents stay separate from every other student.</p>
          </div>
          <form className="login-card" onSubmit={(event) => void login(event)}>
            <p className="section-label">Student sign in</p>
            <label>Email<input name="email" type="email" autoComplete="username" required /></label>
            <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
            {error ? <ErrorNotice error={error} onDismiss={() => setError(null)} /> : null}
            <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Enter workspace"}</button>
          </form>
        </section>
      ) : (
        <>
          <section className="workspace-heading">
            <div><p className="eyebrow">Private workspace</p><h1>Welcome, {user.display_name}.</h1></div>
            <p>{user.email}</p>
          </section>
          <section className="workspace-grid">
            <div className="conversation-panel">
              <div className="panel-heading">
                <div><p className="section-label">Your conversations</p><h2>{conversations.length} saved</h2></div>
              </div>
              <form className="new-conversation" onSubmit={(event) => void createConversation(event)}>
                <input name="title" aria-label="Conversation title" placeholder="Name a new conversation" maxLength={160} required />
                <button type="submit" disabled={busy || chatBusy}>Create</button>
              </form>
              <div className="conversation-list">
                {conversations.map((conversation) => (
                  <button className={selected?.id === conversation.id ? "conversation-item selected" : "conversation-item"} key={conversation.id} type="button" onClick={() => void openConversation(conversation)} disabled={chatBusy}>
                    <strong>{conversation.title}</strong>
                    <small>Updated {new Date(conversation.updated_at).toLocaleString()}</small>
                  </button>
                ))}
                {conversations.length === 0 ? <p className="empty-state">No conversations yet. Create the first one above.</p> : null}
              </div>
              {selected ? (
                <section className="chat-panel" aria-label="Conversation messages">
                  <h3>{selected.title}</h3>
                  {messages.length === 0 && !chatBusy ? <p className="empty-state">Ask your first question.</p> : null}
                  <div className="message-list">
                    {messages.map((message) => (
                      <article className={`message ${message.role}`} key={message.id}>
                        <p className="message-role">{message.role === "user" ? "You" : "Assistant"} · {message.status}</p>
                        {message.content ? <p>{message.content}</p> : null}
                        <SourceList sources={message.sources ?? []} />
                        {message.status === "failed" ? (
                          <div>
                            <ErrorNotice error={chatFailure(message.error_code)} />
                            <button type="button" onClick={() => retry(message)} disabled={chatBusy}>Retry</button>
                          </div>
                        ) : null}
                      </article>
                    ))}
                    {chatBusy ? (
                      <article className="message assistant" aria-live="polite">
                        <p className="message-role">Assistant · {chatStatus}</p>
                        <SourceList sources={liveSources} />
                        <p>{liveAnswer || "Waiting for the model…"}</p>
                      </article>
                    ) : null}
                  </div>
                  <form className="chat-form" onSubmit={submitChat}>
                    <label htmlFor="chat-draft">Your question</label>
                    <textarea id="chat-draft" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={4000} rows={3} required />
                    <button type="submit" disabled={chatBusy || !draft.trim()}>Send message</button>
                  </form>
                </section>
              ) : null}
              {error ? <ErrorNotice error={error} onDismiss={() => setError(null)} /> : null}
            </div>
            <aside className="sidebar-stack">
              <DocumentsPanel key={selected?.id ?? "none"} conversationId={selected?.id ?? null} onError={reportError}
                onDocumentDeleted={() => { if (selected) void loadMessages(selected.id); }} />
              <section className="status-panel">
              <p className="section-label">Server readiness</p>
              <h2>{readiness?.status === "ready" ? "All systems ready" : "Setup needs attention"}</h2>
              {readiness?.status === "ready" ? null : (
                <ErrorNotice error={readiness
                  ? { title: "Server setup needs attention", message: "One or more services are unavailable.", nextStep: "Check the service details below, then select Check again.", status: 503 }
                  : friendlyError(null)} />
              )}
              <div className="service-list">
                {Object.entries(readiness?.components ?? {}).map(([name, component]) => (
                  <div className="service-row" key={name}>
                    <span className={component.ok ? "dot ok" : "dot"} />
                    <span>{labels[name] ?? name}</span>
                    <small>{component.detail}</small>
                  </div>
                ))}
              </div>
              <button className="text-button" type="button" onClick={() => void refreshReadiness()}>Check again</button>
              </section>
            </aside>
          </section>
        </>
      )}
      <footer><span>Secure session · private resources</span><span>PDF chat · saved history</span></footer>
    </main>
  );
}

export default App;
