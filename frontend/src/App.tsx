import { FormEvent, useCallback, useEffect, useState } from "react";
import DocumentsPanel from "./DocumentsPanel";
import SourceList, { type Source } from "./SourceList";



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

type LoginResponse = { user: User; csrf_token: string };

type ChatEvent = { request_id: string; message_id?: string; status?: string; content?: string; error_code?: string; document_id?: string; chunk_id?: string; marker?: string; filename?: string; page_number?: number; quote?: string; unavailable?: boolean };



const labels: Record<string, string> = {

  postgres: "PostgreSQL",

  qdrant: "Qdrant",

  ollama: "Ollama",

};

const chatErrors: Record<string, string> = {

  model_missing: "The chat model is not installed on the server yet.",
  embedding_model_missing: "The embedding model is not installed yet.",
  vector_store_unavailable: "Document search is unavailable.",
  invalid_citation: "The answer contained an unverified citation.",
  source_unavailable: "A source changed while this answer was being written.",

  ollama_unavailable: "Ollama is unavailable. Try again after it starts.",

  generation_timeout: "The model took too long. You can retry this message.",

  queue_timeout: "The request waited too long in the queue.",

  client_disconnected: "The connection closed before the answer finished.",
  interrupted: "The server stopped before the answer finished. You can retry.",

};



function getCookie(name: string): string | null {

  const prefix = `${encodeURIComponent(name)}=`;

  const value = document.cookie.split("; ").find((entry) => entry.startsWith(prefix));

  return value ? decodeURIComponent(value.slice(prefix.length)) : null;

}



async function apiError(response: Response): Promise<string> {

  try {

    const body = (await response.json()) as { detail?: string };

    return body.detail ?? `Request failed (${response.status})`;

  } catch {

    return `Request failed (${response.status})`;

  }

}



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

  const [draft, setDraft] = useState("");

  const [liveAnswer, setLiveAnswer] = useState("");
  const [liveSources, setLiveSources] = useState<Source[]>([]);

  const [chatStatus, setChatStatus] = useState("");

  const [chatBusy, setChatBusy] = useState(false);

  const [loading, setLoading] = useState(true);

  const [busy, setBusy] = useState(false);

  const [error, setError] = useState<string | null>(null);



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

    if (!response.ok) throw new Error(await apiError(response));

    setConversations((await response.json()) as Conversation[]);

  }, []);



  const loadMessages = useCallback(async (id: string) => {

    const response = await fetch(`/api/conversations/${id}/messages`);

    if (!response.ok) throw new Error(await apiError(response));

    setMessages((await response.json()) as Message[]);

  }, []);



  useEffect(() => {

    const bootstrap = async () => {

      await refreshReadiness();

      const response = await fetch("/api/me");

      if (response.ok) {

        setUser((await response.json()) as User);

        await loadConversations();

      }

      setLoading(false);

    };

    void bootstrap().catch((reason: unknown) => {

      setError(reason instanceof Error ? reason.message : "Startup failed");

      setLoading(false);

    });

  }, [loadConversations, refreshReadiness]);



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

      if (!response.ok) throw new Error(await apiError(response));

      const payload = (await response.json()) as LoginResponse;

      setUser(payload.user);

      await loadConversations();

    } catch (reason) {

      setError(reason instanceof Error ? reason.message : "Sign in failed");

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

        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCookie("smart_learning_csrf") ?? "" },

        body: JSON.stringify({ title: form.get("title") }),

      });

      if (!response.ok) throw new Error(await apiError(response));

      const created = (await response.json()) as Conversation;

      formElement.reset();

      await loadConversations();

      setSelected(created);

      setMessages([]);

      setDraft("");

    } catch (reason) {

      setError(reason instanceof Error ? reason.message : "Could not create conversation");

    } finally {

      setBusy(false);

    }

  };



  const openConversation = async (conversation: Conversation) => {

    setSelected(conversation);

    setMessages([]);

    setLiveAnswer("");
    setLiveSources([]);

    setChatStatus("");

    setError(null);

    try {

      await loadMessages(conversation.id);

    } catch (reason) {

      setError(reason instanceof Error ? reason.message : "Could not load messages");

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

        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCookie("smart_learning_csrf") ?? "" },

        body: JSON.stringify({ request_id: requestId, content }),

      });

      if (!response.ok) throw new Error(await apiError(response));

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

        if (name === "failed") setError(chatErrors[payload.error_code ?? ""] ?? "The reply failed. You can retry it.");

        if (name === "complete") setChatStatus("Completed");

      });

      await loadMessages(conversationId);

      await loadConversations();

    } catch (reason) {

      setError(reason instanceof Error ? reason.message : "Chat request failed");

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

    setDraft("");

    void sendChat(selected.id, crypto.randomUUID(), content);

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

        headers: { "X-CSRF-Token": getCookie("smart_learning_csrf") ?? "" },

      });

      if (!response.ok) throw new Error(await apiError(response));

      setUser(null);

      setConversations([]);

      setSelected(null);

      setMessages([]);

    } catch (reason) {

      setError(reason instanceof Error ? reason.message : "Could not log out");

    } finally {

      setBusy(false);

    }

  };



  if (loading) return <main className="loading-screen">Preparing your private workspaceâ€¦</main>;



  return (

    <main>

      <header className="topbar">

        <span>Smart Learning System</span>

        {user ? <button className="text-button" type="button" onClick={() => void logout()} disabled={busy || chatBusy}>Log out</button> : null}

      </header>

      {!user ? (

        <section className="login-layout">

          <div className="hero">

            <p className="eyebrow">Private learning Â· three-VM lab</p>

            <h1>Learn from your own material.</h1>

            <p className="summary">Sign in with the student account created by the administrator. Your conversations and documents stay separate from every other student.</p>

          </div>

          <form className="login-card" onSubmit={(event) => void login(event)}>

            <p className="section-label">Student sign in</p>

            <label>Email<input name="email" type="email" autoComplete="username" required /></label>

            <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>

            {error ? <p className="error">{error}</p> : null}

            <button type="submit" disabled={busy}>{busy ? "Signing inâ€¦" : "Enter workspace"}</button>

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

                        <p className="message-role">{message.role === "user" ? "You" : "Assistant"} Â· {message.status}</p>

                        {message.content ? <p>{message.content}</p> : null}
                        <SourceList sources={message.sources ?? []} />

                        {message.status === "failed" ? (

                          <div>

                            <p className="error">{chatErrors[message.error_code ?? ""] ?? "The reply failed."}</p>

                            <button type="button" onClick={() => retry(message)} disabled={chatBusy}>Retry</button>

                          </div>

                        ) : null}

                      </article>

                    ))}

                    {chatBusy ? (

                      <article className="message assistant" aria-live="polite">

                        <p className="message-role">Assistant Â· {chatStatus}</p>

                        <SourceList sources={liveSources} />
                        <p>{liveAnswer || "Waiting for the modelâ€¦"}</p>

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

              {error ? <p className="error" role="alert">{error}</p> : null}

            </div>

            <aside className="sidebar-stack">
              <DocumentsPanel conversationId={selected?.id ?? null} onError={setError}
                onDocumentDeleted={() => { if (selected) void loadMessages(selected.id); }} />
              <section className="status-panel">

              <p className="section-label">Server readiness</p>

              <h2>{readiness?.status === "ready" ? "All systems ready" : "Setup needs attention"}</h2>

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

      <footer><span>Secure session Â· private resources</span><span>PDF chat Â· saved history</span></footer>

    </main>

  );

}



export default App;
