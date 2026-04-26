"""Streamlit 前端 — 替代 React，功能一一对应。

Chat 多轮对话 / Ask 单轮问答 / Citation 面板 / Thumbs Down 反馈 / 流式输出。
"""

import json
import os

import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://backend:8000")

# ---------------------------------------------------------------------------
# Session state 初始化（必须在页面最顶部）
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []  # [{role, content, citations?, thumbed_down?}]
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "feedback_sent" not in st.session_state:
    st.session_state.feedback_sent = set()  # 已反馈的消息索引


# ---------------------------------------------------------------------------
# SSE 解析 — 手动解析 streaming response，转为 generator
# ---------------------------------------------------------------------------
def parse_sse_stream(response):
    """解析 SSE 流，yield (event_type, payload_dict)。"""
    buffer = ""
    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
        buffer += chunk
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            event_type = None
            data = None
            for line in raw_event.strip().split("\n"):
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data = line[len("data:"):].strip()
            if event_type and data:
                try:
                    yield event_type, json.loads(data)
                except json.JSONDecodeError:
                    pass


def stream_chat(message: str, history: list, conversation_id: str | None):
    """POST /v1/chat/stream，返回 (answer, citations, conversation_id)。

    同时 yield 文本 chunk 给 st.write_stream 逐字渲染。
    """
    body = {
        "message": message,
        "history": history,
        "use_retrieval": True,
        "top_k": 5,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    try:
        resp = requests.post(
            f"{API_BASE_URL}/v1/chat/stream",
            json=body,
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        st.error("无法连接到后端服务，请稍后重试")
        return
    except requests.RequestException as exc:
        st.error(f"请求失败：{exc}")
        return

    citations = []
    new_conversation_id = conversation_id
    full_text = ""

    for event_type, payload in parse_sse_stream(resp):
        if event_type == "citation_data":
            citations = payload.get("citations", [])
        elif event_type == "response_text":
            text = payload.get("text", "")
            full_text += text
            yield text
        elif event_type == "done":
            cid = payload.get("conversation_id")
            if cid:
                new_conversation_id = cid
            # Output safety replacement: if backend flagged the response
            if payload.get("replaced"):
                replacement = payload.get("replacement_text", "")
                full_text = replacement
                st.session_state._output_replaced = True
                st.session_state._replacement_text = replacement

    # 将结果存到 session_state（通过闭包外部变量传递）
    st.session_state._last_citations = citations
    st.session_state._last_conversation_id = new_conversation_id
    st.session_state._last_full_text = full_text


def send_feedback(conversation_id: str | None, message_index: int):
    """POST /v1/feedback — 失败不影响主流程。"""
    if not conversation_id:
        return False
    try:
        resp = requests.post(
            f"{API_BASE_URL}/v1/feedback",
            json={"conversation_id": conversation_id, "message_index": message_index},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 渲染 Citation 面板
# ---------------------------------------------------------------------------
def render_citations(citations: list):
    """在 expander 中显示引用来源。"""
    if not citations:
        return
    with st.expander(f"📄 引用来源 ({len(citations)})"):
        for c in citations:
            filename = c.get("filename", "unknown")
            page = c.get("page", 0)
            snippet = c.get("snippet", "")
            line = f"**{filename}** · 第 {page} 页"
            if snippet:
                line += f" — {snippet}"
            st.markdown(f"- {line}")


# ---------------------------------------------------------------------------
# 渲染消息历史
# ---------------------------------------------------------------------------
def render_messages(messages: list, mode: str = "chat"):
    """渲染所有消息，包含 citation 和 thumbs down 按钮。"""
    for i, msg in enumerate(messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("content"):
                # Citation
                render_citations(msg.get("citations", []))
                # Thumbs Down
                if i not in st.session_state.feedback_sent:
                    if st.button("👎", key=f"fb_{mode}_{i}", help="回答有误或无帮助"):
                        cid = st.session_state.conversation_id if mode == "chat" else None
                        ok = send_feedback(cid, i)
                        if ok:
                            st.session_state.feedback_sent.add(i)
                            st.rerun()
                        else:
                            st.warning("反馈发送失败")
                else:
                    st.button("👎 已反馈", key=f"fb_{mode}_{i}_done", disabled=True)


# ---------------------------------------------------------------------------
# Chat 页面
# ---------------------------------------------------------------------------
def chat_page():
    st.header("RAG Chatbot (Chat)")
    st.caption("多轮对话模式：上下文在前端维护，刷新后清空。")

    render_messages(st.session_state.messages, mode="chat")

    if prompt := st.chat_input("输入你的问题..."):
        # 添加用户消息
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 构造 history
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages[:-1]  # 不含当前这条
        ]

        # 流式渲染 assistant 回答
        with st.chat_message("assistant"):
            # 初始化临时变量
            st.session_state._last_citations = []
            st.session_state._last_conversation_id = st.session_state.conversation_id
            st.session_state._last_full_text = ""
            st.session_state._output_replaced = False
            st.session_state._replacement_text = ""

            generator = stream_chat(
                prompt,
                history,
                st.session_state.conversation_id,
            )
            try:
                full_response = st.write_stream(generator)
            except Exception:
                full_response = st.session_state._last_full_text

            # If output was replaced by safety check, show replacement text
            if st.session_state.get("_output_replaced", False):
                full_response = st.session_state._replacement_text
                st.warning(full_response)

            citations = st.session_state._last_citations
            st.session_state.conversation_id = st.session_state._last_conversation_id

            render_citations(citations)

        # 保存 assistant 消息
        st.session_state.messages.append({
            "role": "assistant",
            "content": full_response or st.session_state._last_full_text,
            "citations": citations,
        })


# ---------------------------------------------------------------------------
# Ask 页面
# ---------------------------------------------------------------------------
def ask_page():
    st.header("RAG Chatbot (Ask)")
    st.caption("单轮提问，不保留对话历史。")

    # Ask 模式：每次新提问清空上一轮
    if "ask_messages" not in st.session_state:
        st.session_state.ask_messages = []

    render_messages(st.session_state.ask_messages, mode="ask")

    if prompt := st.chat_input("输入你的问题..."):
        # 清空上一轮
        st.session_state.ask_messages = [{"role": "user", "content": prompt}]
        st.session_state.feedback_sent = set()

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            st.session_state._last_citations = []
            st.session_state._last_conversation_id = None
            st.session_state._last_full_text = ""
            st.session_state._output_replaced = False
            st.session_state._replacement_text = ""

            generator = stream_chat(prompt, [], None)
            try:
                full_response = st.write_stream(generator)
            except Exception:
                full_response = st.session_state._last_full_text

            if st.session_state.get("_output_replaced", False):
                full_response = st.session_state._replacement_text
                st.warning(full_response)

            citations = st.session_state._last_citations
            render_citations(citations)

        st.session_state.ask_messages.append({
            "role": "assistant",
            "content": full_response or st.session_state._last_full_text,
            "citations": citations,
        })


# ---------------------------------------------------------------------------
# 侧边栏导航 + 路由
# ---------------------------------------------------------------------------
st.set_page_config(page_title="RAG Chatbot", page_icon="💬", layout="wide")

with st.sidebar:
    st.title("💬 RAG Chatbot")
    page = st.radio("导航", ["Chat", "Ask"], label_visibility="collapsed")

    if page == "Chat":
        if st.button("🗑️ 清空对话"):
            st.session_state.messages = []
            st.session_state.conversation_id = None
            st.session_state.feedback_sent = set()
            st.rerun()

if page == "Chat":
    chat_page()
else:
    ask_page()
