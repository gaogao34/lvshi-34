const STORAGE_KEYS = {
  baseUrl: "law-app-base-url",
  model: "law-app-model",
  apiKey: "law-app-api-key",
  extraHeaders: "law-app-extra-headers",
  activeConversation: "law-app-active-conversation",
};

const state = {
  config: loadConfig(),
  conversations: [],
  activeConversationId: null,
  messages: [],
  sources: [],
};

const conversation = document.querySelector("#conversation");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const settingsDialog = document.querySelector("#settings-dialog");
const settingsForm = document.querySelector("#settings-form");
const connectionState = document.querySelector("#connection-state");
const sourceList = document.querySelector("#source-list");
const sourceCount = document.querySelector("#source-count");
const conversationList = document.querySelector("#conversation-list");
const conversationCount = document.querySelector("#conversation-count");
const conversationTitle = document.querySelector("#conversation-title");
const newChatButton = document.querySelector("#new-chat");

function loadConfig() {
  return {
    base_url: localStorage.getItem(STORAGE_KEYS.baseUrl) || "",
    model: localStorage.getItem(STORAGE_KEYS.model) || "",
    api_key: localStorage.getItem(STORAGE_KEYS.apiKey) || "",
    extra_headers: localStorage.getItem(STORAGE_KEYS.extraHeaders) || "",
  };
}

function saveConfig() {
  localStorage.setItem(STORAGE_KEYS.baseUrl, state.config.base_url);
  localStorage.setItem(STORAGE_KEYS.model, state.config.model);
  localStorage.setItem(STORAGE_KEYS.apiKey, state.config.api_key);
  localStorage.setItem(STORAGE_KEYS.extraHeaders, state.config.extra_headers || "");
}

function setConnectionState(label, connected = false) {
  connectionState.innerHTML = `<span class="${connected ? "connected" : ""}"></span>${label}`;
}

function autoResize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatText(text) {
  const normalized = text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .join("\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
  const escaped = escapeHtml(normalized).replaceAll("\n", "<br />");
  return escaped.replace(/\[(\d+)]/g, '<span class="citation">[$1]</span>');
}

function formatDateTime(isoValue) {
  if (!isoValue) return "";
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderWelcome() {
  conversation.innerHTML = `
    <article class="welcome-message" id="welcome-message">
      <div class="welcome-symbol" aria-hidden="true">§</div>
      <p class="eyebrow">依据本地法条库</p>
      <h3>把事情说清楚，我来拆开法律风险。</h3>
      <p>说明时间、双方身份、做了什么、已经发生什么，以及你最担心的结果。</p>
      <div class="suggestions" aria-label="示例问题">
        <button type="button" data-prompt="公司连续两个月没有按时发工资，我可以怎么处理？">拖欠工资</button>
        <button type="button" data-prompt="签了合同后，对方不履行约定，我可以主张什么权利？">合同不履行</button>
        <button type="button" data-prompt="未经本人同意使用我的照片做商业宣传，可能承担什么责任？">肖像被商用</button>
      </div>
    </article>`;
}

function renderMessages(messages) {
  if (!messages.length) {
    renderWelcome();
    return;
  }
  conversation.innerHTML = "";
  messages.forEach((message) => {
    const article = document.createElement("article");
    article.className = `message ${message.role}`;
    article.innerHTML = `
      <div class="message-label">${message.role === "user" ? "你的描述" : "法析"}</div>
      <div class="message-content">${formatText(message.content)}</div>
    `;
    conversation.append(article);
  });
  conversation.scrollTop = conversation.scrollHeight;
}

function renderSources(sources) {
  state.sources = sources || [];
  sourceList.replaceChildren();
  sourceCount.textContent = `${state.sources.length} 条`;
  if (!state.sources.length) {
    sourceList.innerHTML = '<p class="empty-sources">未检索到直接相关的法条。</p>';
    return;
  }
  state.sources.forEach((source, index) => {
    const card = document.createElement("article");
    card.className = "source-card";
    const hierarchy = Object.values(source.hierarchy || {}).join(" / ");
    const pages = source.source_pages.map((page) => `第 ${page} 页`).join("、");
    card.innerHTML = `
      <div class="source-index">${index + 1}</div>
      <div class="source-card-body">
        <div class="source-meta"><span>${source.document_title}</span><span>${source.article}</span></div>
        ${hierarchy ? `<p class="source-hierarchy">${hierarchy}</p>` : ""}
        <p class="source-text">${escapeHtml(source.text)}</p>
        <p class="source-page">${source.source_file} · ${pages}</p>
      </div>
    `;
    sourceList.append(card);
  });
}

function renderConversationList() {
  conversationList.replaceChildren();
  conversationCount.textContent = `${state.conversations.length}`;
  if (!state.conversations.length) {
    conversationList.innerHTML = '<p class="empty-sources">暂无历史对话。</p>';
    return;
  }
  state.conversations.forEach((item) => {
    const button = document.createElement("div");
    const isActive = item.id === state.activeConversationId;
    button.className = `conversation-item${isActive ? " active" : ""}`;
    button.dataset.conversationId = item.id;
    button.tabIndex = 0;
    button.setAttribute("role", "button");
    button.innerHTML = `
      <div class="conversation-item-main">
        <span class="conversation-item-title">${escapeHtml(item.title)}</span>
        <span class="conversation-item-preview">${escapeHtml(item.preview || "尚无内容")}</span>
        <span class="conversation-item-meta">${item.message_count} 条 · ${formatDateTime(item.updated_at)}</span>
      </div>
      <button class="conversation-delete" type="button" data-delete-conversation-id="${item.id}" title="删除对话" aria-label="删除对话">×</button>
    `;
    conversationList.append(button);
  });
}

function updateConversationTitle() {
  const active = state.conversations.find((item) => item.id === state.activeConversationId);
  conversationTitle.textContent = active?.title || "法律问题分析";
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    document.querySelector("#article-count").textContent = `${data.article_count.toLocaleString("zh-CN")} 条法条切片`;
  } catch {
    document.querySelector("#article-count").textContent = "知识库未连接";
  }
}

async function loadConversations(selectId = null) {
  const response = await fetch("/api/conversations");
  const data = await response.json();
  state.conversations = data.conversations || [];
  const availableIds = new Set(state.conversations.map((item) => item.id));
  const preferredId = [selectId, data.active_conversation_id, state.conversations[0]?.id].find((id) => id && availableIds.has(id));
  state.activeConversationId = preferredId || null;
  renderConversationList();
  if (state.activeConversationId) {
    await loadConversation(state.activeConversationId);
  } else {
    renderWelcome();
  }
}

async function loadConversation(conversationId) {
  const response = await fetch(`/api/conversations/${conversationId}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "无法载入会话。");
  state.activeConversationId = data.conversation.id;
  state.messages = data.conversation.messages || [];
  renderMessages(state.messages);
  renderSources([]);
  renderConversationList();
  updateConversationTitle();
  localStorage.setItem(STORAGE_KEYS.activeConversation, state.activeConversationId);
}

async function createConversation() {
  const response = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "新对话" }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "无法新建会话。");
  localStorage.setItem(STORAGE_KEYS.activeConversation, data.conversation.id);
  await loadConversations(data.conversation.id);
}

async function deleteConversation(conversationId) {
  const target = state.conversations.find((item) => item.id === conversationId);
  if (!target) return;
  if (!window.confirm(`确定删除“${target.title}”吗？此操作无法撤销。`)) return;
  const response = await fetch(`/api/conversations/${conversationId}`, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "无法删除会话。");
  state.conversations = data.conversations || [];
  state.activeConversationId = data.active_conversation_id || null;
  localStorage.setItem(STORAGE_KEYS.activeConversation, state.activeConversationId || "");
  renderConversationList();
  if (state.activeConversationId) {
    await loadConversation(state.activeConversationId);
  } else {
    state.messages = [];
    renderMessages([]);
    renderSources([]);
    updateConversationTitle();
  }
}

function openSettings() {
  document.querySelector("#base-url").value = state.config.base_url;
  document.querySelector("#model").value = state.config.model;
  document.querySelector("#api-key").value = state.config.api_key;
  document.querySelector("#extra-headers").value = state.config.extra_headers || "";
  settingsDialog.showModal();
}

function addPendingMessage() {
  const message = document.createElement("article");
  message.className = "message assistant pending";
  message.innerHTML = `
    <div class="message-label">法析</div>
    <div class="message-content"><span class="typing"><i></i><i></i><i></i></span></div>
  `;
  conversation.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
  return message;
}

async function sendMessage(content) {
  if (!state.config.api_key) {
    openSettings();
    return;
  }
  if (!state.activeConversationId) {
    await createConversation();
  }
  const userMessage = { role: "user", content };
  state.messages.push(userMessage);
  renderMessages(state.messages);
  const pending = addPendingMessage();
  input.value = "";
  autoResize();
  composer.classList.add("is-busy");
  setConnectionState("正在分析", true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config: state.config,
        conversation_id: state.activeConversationId,
        message: content,
        extra_headers: state.config.extra_headers,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "分析请求失败，请稍后重试。");
    pending.remove();
    state.messages = data.conversation?.messages || state.messages;
    state.activeConversationId = data.active_conversation_id || state.activeConversationId;
    state.conversations = data.conversations || state.conversations;
    localStorage.setItem(STORAGE_KEYS.activeConversation, state.activeConversationId || "");
    renderMessages(state.messages);
    renderSources(data.sources || []);
    renderConversationList();
    updateConversationTitle();
    setConnectionState("API 已连接", true);
  } catch (error) {
    pending.remove();
    state.messages = state.messages.slice(0, -1);
    renderMessages(state.messages);
    addPendingSystemMessage(`本次请求未完成：${error.message}`);
    setConnectionState("连接需要检查");
  } finally {
    composer.classList.remove("is-busy");
  }
}

function addPendingSystemMessage(text) {
  const message = document.createElement("article");
  message.className = "message assistant";
  message.innerHTML = `
    <div class="message-label">法析</div>
    <div class="message-content">${formatText(text)}</div>
  `;
  conversation.append(message);
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (content && !composer.classList.contains("is-busy")) sendMessage(content);
});

input.addEventListener("input", autoResize);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.querySelector("#open-settings").addEventListener("click", openSettings);
newChatButton.addEventListener("click", async () => {
  if (composer.classList.contains("is-busy")) return;
  await createConversation();
  setConnectionState("新会话已创建", true);
  input.focus();
});

conversationList.addEventListener("click", async (event) => {
  const deleteButton = event.target.closest("[data-delete-conversation-id]");
  if (deleteButton) {
    event.preventDefault();
    event.stopPropagation();
    if (!composer.classList.contains("is-busy")) {
      try {
        await deleteConversation(deleteButton.dataset.deleteConversationId);
      } catch (error) {
        addPendingSystemMessage(`删除失败：${error.message}`);
      }
    }
    return;
  }
  const button = event.target.closest("[data-conversation-id]");
  if (!button || composer.classList.contains("is-busy")) return;
  const id = button.dataset.conversationId;
  if (id === state.activeConversationId) return;
  await loadConversation(id);
});

conversationList.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const item = event.target.closest("[data-conversation-id]");
  if (!item || composer.classList.contains("is-busy")) return;
  event.preventDefault();
  const id = item.dataset.conversationId;
  if (id !== state.activeConversationId) await loadConversation(id);
});

conversation.addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (!button) return;
  input.value = button.dataset.prompt || "";
  autoResize();
  input.focus();
});

settingsForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const rawExtraHeaders = document.querySelector("#extra-headers").value.trim();
  if (rawExtraHeaders) {
    try {
      const parsed = JSON.parse(rawExtraHeaders);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("额外请求头必须是 JSON 对象。");
      }
    } catch (error) {
      window.alert(`API 设置未保存：${error.message}`);
      return;
    }
  }
  state.config = {
    base_url: document.querySelector("#base-url").value.trim(),
    model: document.querySelector("#model").value.trim(),
    api_key: document.querySelector("#api-key").value.trim(),
    extra_headers: rawExtraHeaders,
  };
  if (Object.values(state.config).some((value) => !value)) return;
  saveConfig();
  settingsDialog.close();
  setConnectionState("API 已配置", true);
  input.focus();
});

async function bootstrap() {
  await loadStatus();
  try {
    await loadConversations(localStorage.getItem(STORAGE_KEYS.activeConversation) || null);
  } catch {
    renderWelcome();
  }
  if (state.config.base_url || state.config.model || state.config.api_key) {
    setConnectionState(state.config.api_key ? "API 已配置" : "等待补全 API Key");
  }
}

bootstrap();
autoResize();
