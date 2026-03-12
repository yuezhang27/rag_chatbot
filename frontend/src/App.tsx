import React, { useState } from "react";

type Role = "user" | "assistant";

interface Citation {
  doc_id: number;
  title: string;
  snippet: string;
}

interface RetrievedChunk {
  doc_id: number;
  title: string;
  chunk: string;
}

interface ThoughtProcess {
  chunks: RetrievedChunk[];
  prompt: string;
}

interface Message {
  id: string;
  role: Role;
  content: string;
  citations?: Citation[];
  thoughtProcess?: ThoughtProcess;
}

const API_BASE = "http://localhost:8000";

async function streamSSE(
  url: string,
  body: any,
  onThought: (tp: ThoughtProcess) => void,
  onToken: (text: string) => void,
  onDone: (data: { citations: Citation[] }) => void
) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sepIndex;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const lines = rawEvent.split("\n");
      let event: string | null = null;
      let data: string | null = null;
      for (const line of lines) {
        if (line.startsWith("event:")) {
          event = line.replace("event:", "").trim();
        } else if (line.startsWith("data:")) {
          data = line.replace("data:", "").trim();
        }
      }
      if (!event || !data) continue;
      const payload = JSON.parse(data);
      if (event === "thought_process") {
        onThought(payload as ThoughtProcess);
      } else if (event === "token") {
        onToken(payload.text || "");
      } else if (event === "done") {
        onDone({ citations: payload.citations || [] });
      }
    }
  }
}

const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const sendMessage = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;
    setError(null);

    const userMessage: Message = {
      id: `${Date.now()}-user`,
      role: "user",
      content: trimmed,
    };
    const history = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const assistantId = `${Date.now()}-assistant`;

    setMessages((prev) => [...prev, userMessage, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setLoading(true);

    try {
      await streamSSE(
        `${API_BASE}/v1/chat/stream`,
        {
          question: trimmed,
          use_retrieval: true,
          top_k: 3,
          conversation_history: history,
        },
        (tp) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, thoughtProcess: tp } : m))
          );
        },
        (text) => {
          if (!text) return;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: (m.content || "") + text } : m
            )
          );
        },
        ({ citations }) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, citations } : m))
          );
        }
      );
    } catch (e: any) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="app">
      <header className="app-header">RAG Chatbot (Chat)</header>
      <main className="chat-container">
        <div className="messages">
          {messages.map((m) => (
            <div key={m.id} className={`message-row ${m.role}`}>
              <div className="bubble">
                <div className="content">{m.content}</div>
                {m.role === "assistant" && (
                  <>
                    {m.citations && m.citations.length > 0 && (
                      <div className="citations">
                        <details>
                          <summary>Citations ({m.citations.length})</summary>
                          <ul>
                            {m.citations.map((c) => (
                              <li key={c.doc_id}>
                                <strong>{c.title}</strong>: {c.snippet}
                              </li>
                            ))}
                          </ul>
                        </details>
                      </div>
                    )}
                    {m.thoughtProcess && (
                      <button
                        className="thought-btn"
                        onClick={() =>
                          setExpandedId((prev) => (prev === m.id ? null : m.id))
                        }
                      >
                        {expandedId === m.id ? "隐藏思考过程" : "查看思考过程"}
                      </button>
                    )}
                    {m.thoughtProcess && expandedId === m.id && (
                      <div className="thought-panel">
                        <div className="thought-section">
                          <h4>Prompt</h4>
                          <pre>{m.thoughtProcess.prompt}</pre>
                        </div>
                        <div className="thought-section">
                          <h4>Retrieved Chunks</h4>
                          <ul>
                            {m.thoughtProcess.chunks.map((c) => (
                              <li key={c.doc_id}>
                                <strong>{c.title}</strong>:{" "}
                                {c.chunk.length > 200 ? c.chunk.slice(0, 200) + "..." : c.chunk}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          {messages.length === 0 && (
            <div className="placeholder">上传 PDF 后，在这里提问以查看带引用的回答。</div>
          )}
        </div>
        <div className="input-area">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题，按 Enter 发送（Shift+Enter 换行）"
          />
          <button onClick={sendMessage} disabled={loading}>
            {loading ? "发送中..." : "发送"}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </main>
    </div>
  );
};

const AskPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const send = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;
    setError(null);

    const userMessage: Message = {
      id: `${Date.now()}-user`,
      role: "user",
      content: trimmed,
    };
    const assistantId = `${Date.now()}-assistant`;

    setMessages([userMessage, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setLoading(true);

    try {
      await streamSSE(
        `${API_BASE}/v1/ask/stream`,
        {
          question: trimmed,
          use_retrieval: true,
          top_k: 3,
        },
        (tp) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, thoughtProcess: tp } : m))
          );
        },
        (text) => {
          if (!text) return;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: (m.content || "") + text } : m
            )
          );
        },
        ({ citations }) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, citations } : m))
          );
        }
      );
    } catch (e: any) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="app">
      <header className="app-header">RAG Chatbot (Ask)</header>
      <main className="chat-container">
        <div className="messages">
          {messages.map((m) => (
            <div key={m.id} className={`message-row ${m.role}`}>
              <div className="bubble">
                <div className="content">{m.content}</div>
                {m.role === "assistant" && (
                  <>
                    {m.citations && m.citations.length > 0 && (
                      <div className="citations">
                        <details>
                          <summary>Citations ({m.citations.length})</summary>
                          <ul>
                            {m.citations.map((c) => (
                              <li key={c.doc_id}>
                                <strong>{c.title}</strong>: {c.snippet}
                              </li>
                            ))}
                          </ul>
                        </details>
                      </div>
                    )}
                    {m.thoughtProcess && (
                      <button
                        className="thought-btn"
                        onClick={() =>
                          setExpandedId((prev) => (prev === m.id ? null : m.id))
                        }
                      >
                        {expandedId === m.id ? "隐藏思考过程" : "查看思考过程"}
                      </button>
                    )}
                    {m.thoughtProcess && expandedId === m.id && (
                      <div className="thought-panel">
                        <div className="thought-section">
                          <h4>Prompt</h4>
                          <pre>{m.thoughtProcess.prompt}</pre>
                        </div>
                        <div className="thought-section">
                          <h4>Retrieved Chunks</h4>
                          <ul>
                            {m.thoughtProcess.chunks.map((c) => (
                              <li key={c.doc_id}>
                                <strong>{c.title}</strong>:{" "}
                                {c.chunk.length > 200 ? c.chunk.slice(0, 200) + "..." : c.chunk}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          {messages.length === 0 && (
            <div className="placeholder">单轮提问，不保留对话历史。</div>
          )}
        </div>
        <div className="input-area">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题，按 Enter 发送（Shift+Enter 换行）"
          />
          <button onClick={send} disabled={loading}>
            {loading ? "发送中..." : "发送"}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </main>
    </div>
  );
};

const App: React.FC = () => {
  const path = window.location.pathname;
  if (path.startsWith("/ask")) {
    return <AskPage />;
  }
  return <ChatPage />;
};

export default App;

