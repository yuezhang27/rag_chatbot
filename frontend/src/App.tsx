import React, { useState } from "react";

type Role = "user" | "assistant";

interface Citation {
  filename: string;
  page: number;
  snippet?: string;
}

interface Message {
  id: string;
  role: Role;
  content: string;
  citations?: Citation[];
  thumbedDown?: boolean;

  // 保留旧字段注释，不直接删除：
  // 原本对应调试需求（展示 thought_process: prompt + retrieved chunks）。
  // 现在按 PRD 已移出用户功能范围，保留注释供后续回看演进。
  // thoughtProcess?: ThoughtProcess;
}

const API_BASE = (import.meta as any).env?.VITE_API_BASE || "http://localhost:8000";

async function streamSSE(
  url: string,
  body: any,
  onCitation: (citations: Citation[]) => void,
  onToken: (text: string) => void,
  onDone: (data: { conversation_id?: string }) => void
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
      if (event === "citation_data") {
        onCitation(payload.citations || []);
      } else if (event === "response_text") {
        onToken(payload.text || "");
      } else if (event === "done") {
        onDone({ conversation_id: payload.conversation_id });
      }

      // 旧协议保留注释，不直接删除：
      // if (event === "thought_process") { ... }
      // else if (event === "token") { ... }
    }
  }
}

async function sendFeedback(conversationId: string | null, messageIndex: number) {
  if (!conversationId) return;
  try {
    await fetch(`${API_BASE}/v1/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, message_index: messageIndex }),
    });
  } catch (_) {
    // feedback loss is acceptable
  }
}

const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);

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
          conversation_id: conversationId,
          message: trimmed,
          history,
          use_retrieval: true,
          top_k: 5,
        },
        (citations) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, citations } : m))
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
        ({ conversation_id }) => {
          if (conversation_id) {
            setConversationId(conversation_id);
          }
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
          {messages.map((m, msgIndex) => (
            <div key={m.id} className={`message-row ${m.role}`}>
              <div className=”bubble”>
                <div className=”content”>{m.content}</div>
                {m.role === “assistant” && m.citations && m.citations.length > 0 && (
                  <div className=”citations”>
                    <details>
                      <summary>Citations ({m.citations.length})</summary>
                      <ul>
                        {m.citations.map((c, index) => (
                          <li key={`${c.filename}-${c.page}-${index}`}>
                            <strong>{c.filename}</strong> · 第 {c.page} 页
                            {c.snippet && <span className=”citation-snippet”> — {c.snippet}</span>}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                )}
                {m.role === “assistant” && m.content && (
                  <button
                    className={`feedback-btn${m.thumbedDown ? “ thumbed-down” : “”}`}
                    disabled={m.thumbedDown}
                    onClick={() => {
                      setMessages((prev) =>
                        prev.map((msg) => msg.id === m.id ? { ...msg, thumbedDown: true } : msg)
                      );
                      sendFeedback(conversationId, msgIndex);
                    }}
                    title=”回答有误或无帮助”
                  >
                    👎
                  </button>
                )}
              </div>
            </div>
          ))}

          {messages.length === 0 && (
            <div className=”placeholder”>多轮对话模式：上下文在前端维护，刷新后清空。</div>
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
      // Ask = history 为空的 Chat，统一走同一后端接口（ADR 决策）
      await streamSSE(
        `${API_BASE}/v1/chat/stream`,
        {
          message: trimmed,
          history: [],
          use_retrieval: true,
          top_k: 5,
        },
        (citations) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, citations } : m))
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
        () => {
          // Ask 模式不保留上下文，不使用 conversation_id。
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
          {messages.map((m, msgIndex) => (
            <div key={m.id} className={`message-row ${m.role}`}>
              <div className="bubble">
                <div className="content">{m.content}</div>
                {m.role === "assistant" && m.citations && m.citations.length > 0 && (
                  <div className="citations">
                    <details>
                      <summary>Citations ({m.citations.length})</summary>
                      <ul>
                        {m.citations.map((c, index) => (
                          <li key={`${c.filename}-${c.page}-${index}`}>
                            <strong>{c.filename}</strong> · 第 {c.page} 页
                            {c.snippet && <span className="citation-snippet"> — {c.snippet}</span>}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                )}
                {m.role === "assistant" && m.content && (
                  <button
                    className={`feedback-btn${m.thumbedDown ? " thumbed-down" : ""}`}
                    disabled={m.thumbedDown}
                    onClick={() => {
                      setMessages((prev) =>
                        prev.map((msg) => msg.id === m.id ? { ...msg, thumbedDown: true } : msg)
                      );
                      sendFeedback(null, msgIndex);
                    }}
                    title="回答有误或无帮助"
                  >
                    👎
                  </button>
                )}
              </div>
            </div>
          ))}
          {messages.length === 0 && <div className="placeholder">单轮提问，不保留对话历史。</div>}
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
