const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const clearButton = document.querySelector("#clear-chat");
const initialMessage = messages.innerHTML;
const conversationStorageKey = "supermarket-agent-conversation-id";

function createConversationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

let conversationId = sessionStorage.getItem(conversationStorageKey) || createConversationId();
sessionStorage.setItem(conversationStorageKey, conversationId);

function addMessage(text, type, extraClass = "", meta = "") {
  const article = document.createElement("article");
  article.className = `message ${type}-message ${extraClass}`.trim();

  if (type === "assistant") {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "AI";
    article.appendChild(avatar);
  }

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  if (meta) {
    const metaLine = document.createElement("p");
    metaLine.className = "message-meta";
    metaLine.textContent = meta;
    body.appendChild(metaLine);
  }
  article.appendChild(body);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function setBusy(busy) {
  sendButton.disabled = busy;
  input.disabled = busy;
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

async function ask(question) {
  const cleanQuestion = question.trim();
  if (!cleanQuestion || sendButton.disabled) return;

  addMessage(cleanQuestion, "user");
  input.value = "";
  resizeInput();
  setBusy(true);

  const loading = addMessage("LLM 正在理解问题并准备只读查询…", "assistant", "loading");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: cleanQuestion, conversationId }),
    });
    const payload = await response.json();
    loading.remove();
    if (!response.ok) throw new Error(payload.error || "查询失败");
    if (payload.conversationId) {
      conversationId = payload.conversationId;
      sessionStorage.setItem(conversationStorageKey, conversationId);
    }
    let meta = "";
    if (payload.meta?.mode === "validated_llm") {
      if (payload.meta.toolExecuted) {
        meta = `LLM 选择 ${payload.meta.tool} · 参数已校验 · 本机只读执行`;
        if (payload.meta.tool === 'run_business_review') meta = '经营概览与变化拆解 · 已核对全店与类别、商品金额变化';
      } else if (payload.meta.decision === "clarify") {
        meta = "LLM 判断需要先澄清 · 未执行数据库工具";
      } else {
        meta = "能力边界拒答 · 未执行数据库工具";
      }
    }
    if (payload.meta?.contextUsed) meta += `${meta ? " · " : ""}已使用上一轮上下文`;
    if (payload.report) {
      const message = addMessage('', 'assistant', 'report-message');
      message.querySelector('.message-body').appendChild(buildBusinessReport(payload.report, payload.answer));
      requestAnimationFrame(() => {
        messages.scrollTo({top: messages.scrollTop + message.getBoundingClientRect().top - messages.getBoundingClientRect().top, behavior: 'instant'});
      });
    } else {
      addMessage(payload.answer, "assistant", "", meta);
    }
  } catch (error) {
    loading.remove();
    addMessage(error.message || "查询失败，请稍后重试。", "assistant", "error-message");
  } finally {
    setBusy(false);
    input.focus({preventScroll: true});
  }
}

async function loadStatus() {
  const dot = document.querySelector("#status-dot");
  const label = document.querySelector("#status-label");
  const range = document.querySelector("#data-range");
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    if (!response.ok || !payload.ready) throw new Error(payload.error);
    dot.className = "status-dot ready";
    label.textContent = payload.agentMode === "validated_llm"
      ? "LLM 与数据库已连接"
      : "数据库已连接（规则模式）";
    range.textContent = `覆盖 ${payload.earliest} 至 ${payload.latestTimestamp}；趋势截止 ${payload.latestCompleteDate}。`;
  } catch (error) {
    dot.className = "status-dot error";
    label.textContent = "数据库未连接";
    range.textContent = error.message || "请确认本机 PostgreSQL 正在运行。";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener("input", resizeInput);

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => ask(button.dataset.question));
});

clearButton.addEventListener("click", () => {
  messages.innerHTML = initialMessage;
  conversationId = createConversationId();
  sessionStorage.setItem(conversationStorageKey, conversationId);
  input.focus();
});

loadStatus();
input.focus();
