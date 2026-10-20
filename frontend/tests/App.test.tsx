import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../src/App";

const conversations = ["A", "B"].map((id) => ({
  id, title: `Conversation ${id}`, created_at: "2026-10-19T00:00:00Z", updated_at: "2026-10-19T00:00:00Z",
}));
const json = (body: unknown, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json", ...headers },
});
const history = (id: string, content: string) => [{
  id, request_id: id, role: "assistant", status: "completed", content,
  error_code: null, created_at: "2026-10-19T00:00:00Z", sources: [],
}];
const terminal = (name = "complete") => new Response(
  `event: ${name}\ndata: ${JSON.stringify({ request_id: "request", content: "Answer", error_code: "ollama_unavailable" })}\n\n`,
  { headers: { "Content-Type": "text/event-stream" } },
);
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
let messages: (url: string, options?: RequestInit) => Promise<Response>;

beforeEach(() => {
  messages = async () => json([]);
  vi.stubGlobal("fetch", vi.fn(async (input: string, options?: RequestInit) => {
    if (input.endsWith("/messages")) return messages(input, options);
    if (input === "/api/ready") return json({ status: "ready", components: {} });
    if (input === "/api/me") return json({ id: "student", email: "student@example.test", display_name: "Student" });
    if (input === "/api/conversations") return json(conversations);
    if (input === "/api/documents" || input.endsWith("/documents")) return json([]);
    throw new Error(`Unexpected request: ${input}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function open(id = "A") {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`Conversation ${id}`) }));
  await screen.findByRole("heading", { name: `Conversation ${id}` });
}
function enter(content: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: content } });
}
function submit() { fireEvent.click(screen.getByRole("button", { name: "Send message" })); }

test("PDF search sends QUERY JSON and renders a passage with its page link", async () => {
  const ordinaryFetch = fetch;
  const searchRequest = vi.fn(async (_options?: RequestInit) => json([{
    chunk_id: "chunk", document_id: "doc", marker: "S1", filename: "networking.pdf",
    page_number: 2, quote: "TCP uses acknowledgements.",
  }]));
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/documents/search") return searchRequest(options);
    return ordinaryFetch(url, options);
  });
  await open();
  fireEvent.change(screen.getByLabelText("Search my PDFs"), { target: { value: "  TCP reliability  " } });
  fireEvent.click(screen.getByRole("button", { name: "Search PDFs" }));
  await screen.findByText("TCP uses acknowledgements.");
  expect(searchRequest).toHaveBeenCalledWith({
    method: "QUERY", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: "TCP reliability", limit: 5 }),
  });
  expect(screen.getByRole("link", { name: "[S1] networking.pdf, page 2" }).getAttribute("href"))
    .toBe("/api/documents/doc/file#page=2");
});

test("PDF search shows an empty result and handles service errors", async () => {
  const ordinaryFetch = fetch;
  let failed = false;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/documents/search") return failed ? json({ detail: "vector_store_unavailable" }, 503) : json([]);
    return ordinaryFetch(url, options);
  });
  await open();
  expect((screen.getByRole("button", { name: "Search PDFs" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Search my PDFs"), { target: { value: "TCP" } });
  fireEvent.click(screen.getByRole("button", { name: "Search PDFs" }));
  await screen.findByText("No matching passages in your indexed PDFs.");
  failed = true;
  fireEvent.click(screen.getByRole("button", { name: "Search PDFs" }));
  await screen.findByText("Service unavailable");
  expect((screen.getByLabelText("Search my PDFs") as HTMLInputElement).value).toBe("TCP");
  expect(screen.queryByText("No matching passages in your indexed PDFs.")).toBeNull();
});

test("a late response cannot replace the newly selected conversation", async () => {
  const a = deferred<Response>();
  messages = async (url) => url.includes("/A/") ? a.promise : json(history("B", "B reply"));
  await open();
  fireEvent.click(screen.getByRole("button", { name: /Conversation B/ }));
  await screen.findByText("B reply");
  await act(async () => { a.resolve(json(history("A", "A reply"))); await a.promise; });
  expect(screen.queryByText("A reply")).toBeNull();
  expect(screen.getByText("B reply")).toBeTruthy();
});

test("a stale conversation error is ignored", async () => {
  const a = deferred<Response>();
  messages = async (url) => url.includes("/A/") ? a.promise : json(history("B", "B reply"));
  await open();
  fireEvent.click(screen.getByRole("button", { name: /Conversation B/ }));
  await screen.findByText("B reply");
  await act(async () => { a.resolve(json({ detail: "Not found" }, 404)); await a.promise; });
  expect(screen.queryByText("Item unavailable")).toBeNull();
});

test.each([429, 503])("HTTP %s preserves a rejected question and its request ID", async (status) => {
  const requests: string[] = [];
  messages = async (_url, options) => {
    if (options?.method !== "POST") return json([]);
    requests.push(JSON.parse(options.body as string).request_id);
    return json({ detail: "Rejected" }, status);
  };
  await open();
  enter("Keep this question"); submit();
  await screen.findByRole("alert");
  await waitFor(() => expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("Keep this question");
  submit();
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toBe(requests[0]);
});

test("an uncertain network failure reuses the UUID and terminal success clears the draft", async () => {
  const requests: string[] = [];
  messages = async (_url, options) => {
    if (options?.method !== "POST") return json([]);
    requests.push(JSON.parse(options.body as string).request_id);
    if (requests.length === 1) throw new TypeError("Network lost before response headers");
    return terminal();
  };
  await open(); enter("Only once"); submit();
  await screen.findByText("Connection lost");
  await waitFor(() => expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false));
  submit();
  await waitFor(() => expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe(""));
  expect(requests).toHaveLength(2);
  expect(requests[1]).toBe(requests[0]);
});

test("a completed reply preserves edits typed while waiting", async () => {
  const reply = deferred<Response>();
  messages = async (_url, options) => options?.method === "POST" ? reply.promise : json([]);
  await open(); enter("First question"); submit(); enter("Next question");
  await act(async () => { reply.resolve(terminal()); await reply.promise; });
  await waitFor(() => expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("Next question");
});

test("a truncated accepted stream retains the question and retry UUID", async () => {
  const requests: string[] = [];
  messages = async (_url, options) => {
    if (options?.method !== "POST") return json([]);
    requests.push(JSON.parse(options.body as string).request_id);
    return requests.length === 1
      ? new Response('event: delta\ndata: {"request_id":"request","content":"Partial"}\n\n')
      : terminal();
  };
  await open(); enter("Retry the accepted request"); submit();
  await screen.findByText("Connection lost");
  await waitFor(() => expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("Retry the accepted request");
  submit();
  await waitFor(() => expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe(""));
  expect(requests[1]).toBe(requests[0]);
});

test("a terminal model failure keeps the saved question out of the composer", async () => {
  let posted = false;
  messages = async (_url, options) => {
    if (options?.method === "POST") { posted = true; return terminal("failed"); }
    return json(posted ? [{
      ...history("failed", "")[0], status: "failed", error_code: "ollama_unavailable",
    }] : []);
  };
  await open(); enter("Saved question"); submit();
  await screen.findByRole("button", { name: "Retry" });
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("");
});

test("login throttling displays its Retry-After guidance instead of a chat error", async () => {
  const ordinaryFetch = fetch;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/me") return json({}, 401);
    if (url === "/api/login") return json({ detail: "Too many sign-in attempts" }, 429, { "Retry-After": "60" });
    return ordinaryFetch(url, options);
  });
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "student@example.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "test-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Enter workspace" }));
  await screen.findByText("Sign in temporarily limited");
  expect(screen.getByText("Wait 60 seconds, then try signing in again.")).toBeTruthy();
  expect(screen.queryByText("Chat is busy")).toBeNull();
});

test("drafts remain associated with their own conversations", async () => {
  await open(); enter("A draft");
  fireEvent.click(screen.getByRole("button", { name: /Conversation B/ }));
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("");
  enter("B draft");
  fireEvent.click(screen.getByRole("button", { name: /Conversation A/ }));
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("A draft");
});

function signIn() {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "student@example.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "test-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Enter workspace" }));
}

test("an expired session returns to sign-in and keeps the draft for the same student", async () => {
  const ordinaryFetch = fetch;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/login") return json({ user: { id: "student", email: "student@example.test", display_name: "Student" }, csrf_token: "csrf" });
    return ordinaryFetch(url, options);
  });
  const requests: string[] = [];
  messages = async (_url, options) => {
    if (options?.method !== "POST") return json([]);
    requests.push(JSON.parse(options.body as string).request_id);
    return requests.length === 1 ? json({ detail: "Authentication required" }, 401) : terminal();
  };
  await open(); enter("Keep this question"); submit();
  await screen.findByLabelText("Email");
  expect(screen.getByText("Sign in required")).toBeTruthy();
  expect(screen.queryByLabelText("Your question")).toBeNull();
  signIn();
  fireEvent.click(await screen.findByRole("button", { name: /Conversation A/ }));
  await screen.findByRole("heading", { name: "Conversation A" });
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("Keep this question");
  submit();
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toBe(requests[0]);
});

test("a different student signing in after expiry does not inherit drafts", async () => {
  const ordinaryFetch = fetch;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/login") return json({ user: { id: "other", email: "other@example.test", display_name: "Other" }, csrf_token: "csrf" });
    return ordinaryFetch(url, options);
  });
  messages = async (_url, options) => options?.method === "POST" ? json({ detail: "Authentication required" }, 401) : json([]);
  await open(); enter("Private draft"); submit();
  await screen.findByLabelText("Email");
  signIn();
  fireEvent.click(await screen.findByRole("button", { name: /Conversation A/ }));
  await screen.findByRole("heading", { name: "Conversation A" });
  expect((screen.getByLabelText("Your question") as HTMLTextAreaElement).value).toBe("");
});

test("logging out of an already expired session still signs the student out", async () => {
  const ordinaryFetch = fetch;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/logout") return json({ detail: "Authentication required" }, 401);
    return ordinaryFetch(url, options);
  });
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Log out" }));
  await screen.findByLabelText("Email");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a 401 from background document polling returns to sign-in", async () => {
  const ordinaryFetch = fetch;
  let expired = false;
  vi.stubGlobal("fetch", async (url: string, options?: RequestInit) => {
    if (url === "/api/documents") {
      return expired ? json({ detail: "Authentication required" }, 401)
        : json([{ id: "doc", original_filename: "a.pdf", display_name: "a.pdf", status: "indexing", error_code: null, page_count: null, size_bytes: 1 }]);
    }
    return ordinaryFetch(url, options);
  });
  await open();
  await screen.findByText("a.pdf");
  expired = true;
  await screen.findByLabelText("Email", undefined, { timeout: 5000 });
  expect(screen.getByText("Sign in required")).toBeTruthy();
});
