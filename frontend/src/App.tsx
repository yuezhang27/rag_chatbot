import React, { useState } from "react";

type Role = "user" | "assistant";

interface Citation {
  doc_id: number;
  title: string;
  snippet: string;
}

interface Message {
  id: string;
  role: Role;
  content: string;
  citations?: Citation[];
}

const API_BASE = "http://localhost:8000";

const App: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/v1/chat/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          use_retrieval: true,
          top_k: 3,
          conversation_history: history,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const assistantMessage: Message = {
        id: `${Date.now()}-assistant`,
        role: "assistant",
        content: data.answer,
        citations: data.citations || [],
      };
      setMessages((prev) => [...prev, assistantMessage]);
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
      <header className="app-header">RAG Chatbot</header>
      <main className="chat-container">
        <div className="messages">
          {messages.map((m) => (
            <div key={m.id} className={`message-row ${m.role}`}>
              <div className="bubble">
                <div className="content">{m.content}</div>
                {m.role === "assistant" && m.citations && m.citations.length > 0 && (
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

export default App;

