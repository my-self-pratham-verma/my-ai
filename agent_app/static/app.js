const conversation = document.getElementById("conversation");
const promptInput = document.getElementById("prompt");
const composer = document.getElementById("composer");
const sendButton = document.getElementById("send");
const connection = document.querySelector(".connection");
const connectionText = document.getElementById("connection-text");
const responseCount = document.getElementById("response-count");
const microphoneButton = document.getElementById("microphone");
const voiceOutputButton = document.getElementById("voice-output");
const voicePanel = document.getElementById("voice-panel");
const voiceTitle = document.getElementById("voice-title");
const voiceStatus = document.getElementById("voice-status");

const sessionKey = "pratham-agent-session-id";
const sessionId = sessionStorage.getItem(sessionKey) || crypto.randomUUID();
sessionStorage.setItem(sessionKey, sessionId);

let requestInProgress = false;
let activeAssistantBody = null;
let thinkingIndicator = null;
let completedResponses = 0;
const toolCards = new Map();
const SpeechRecognitionApi = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isListening = false;
let isSpeaking = false;
let voiceOutputEnabled = false;

function setVoiceStatus(title, status, state = "idle") {
  voiceTitle.textContent = title;
  voiceStatus.textContent = status;
  voicePanel.dataset.state = state;
  microphoneButton.classList.toggle("listening", state === "listening");
  microphoneButton.setAttribute("aria-pressed", String(state === "listening"));
}

function updateVoiceOutputButton() {
  voiceOutputButton.classList.toggle("active", voiceOutputEnabled);
  voiceOutputButton.setAttribute("aria-pressed", String(voiceOutputEnabled));
  voiceOutputButton.textContent = voiceOutputEnabled ? "🔊 Voice on" : "🔈 Voice off";
  voiceOutputButton.title = voiceOutputEnabled ? "Mute voice responses" : "Enable voice responses";
}

function plainTextForSpeech(markdown) {
  return normalizeEncoding(markdown)
    .replace(/```[\s\S]*?```/g, " Code example omitted. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^[#>*-]+\s*/gm, "")
    .replace(/^\d+[.)]\s*/gm, "")
    .replace(/[*_]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function speakResponse(markdown) {
  if (!voiceOutputEnabled || !("speechSynthesis" in window)) return;

  const text = plainTextForSpeech(markdown);
  if (!text) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 6000));
  utterance.lang = navigator.language || "en-US";
  utterance.rate = 1;
  utterance.onstart = () => {
    isSpeaking = true;
    setVoiceStatus("Agent is speaking", "Reading the final response aloud.", "speaking");
  };
  utterance.onend = () => {
    isSpeaking = false;
    setVoiceStatus("Voice assistant", "Press the mic to speak your next request.");
  };
  utterance.onerror = () => {
    isSpeaking = false;
    setVoiceStatus("Voice assistant", "Voice playback could not start in this browser.", "error");
  };
  window.speechSynthesis.speak(utterance);
}

function createRecognition() {
  if (!SpeechRecognitionApi) return null;
  const browserRecognition = new SpeechRecognitionApi();
  browserRecognition.lang = navigator.language || "en-US";
  browserRecognition.continuous = false;
  browserRecognition.interimResults = true;
  let finalTranscript = "";

  browserRecognition.onstart = () => {
    isListening = true;
    setVoiceStatus("Listening…", "Speak naturally. Your request will send when you stop.", "listening");
  };
  browserRecognition.onresult = event => {
    let interimTranscript = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) {
        finalTranscript += transcript;
      } else {
        interimTranscript += transcript;
      }
    }
    promptInput.value = `${finalTranscript} ${interimTranscript}`.trim();
    resizePrompt();
    updateComposerState();
  };
  browserRecognition.onerror = event => {
    isListening = false;
    const message = event.error === "not-allowed"
      ? "Microphone permission was denied. Allow it in your browser settings."
      : `Speech recognition stopped: ${event.error}.`;
    setVoiceStatus("Voice assistant", message, "error");
  };
  browserRecognition.onend = () => {
    const message = finalTranscript.trim();
    isListening = false;
    if (message && !requestInProgress) {
      setVoiceStatus("Voice assistant", "Sending your spoken request…", "processing");
      submitMessage(message);
    } else if (!requestInProgress) {
      setVoiceStatus("Voice assistant", "Press the mic to speak your next request.");
    }
  };

  return browserRecognition;
}

function startVoiceInput() {
  if (!SpeechRecognitionApi) {
    setVoiceStatus("Voice unavailable", "Use Chrome, Edge, or Safari with microphone permission enabled.", "error");
    return;
  }
  if (requestInProgress) {
    setVoiceStatus("Agent is working", "You can type a follow-up after the current response finishes.", "processing");
    return;
  }
  if (isListening) {
    recognition?.stop();
    return;
  }

  window.speechSynthesis?.cancel();
  voiceOutputEnabled = true;
  updateVoiceOutputButton();
  recognition = createRecognition();
  recognition.start();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeEncoding(text) {
  // The backend now decodes SSE chunks as UTF-8. These replacements also make
  // existing malformed provider fragments readable when a stream was started
  // before the server update.
  const replacements = [
    ["â€¢", "•"], ["â€”", "—"], ["â€“", "–"], ["â€™", "’"],
    ["â€˜", "‘"], ["â€œ", "“"], ["â€", "”"], ["Â·", "·"], ["Â ", " "],
  ];

  return replacements.reduce(
    (normalized, [invalid, valid]) => normalized.split(invalid).join(valid),
    text,
  );
}

function renderInline(text) {
  const codeTokens = [];
  let rendered = escapeHtml(text).replace(/`([^`]+)`/g, (_match, code) => {
    const token = `@@INLINE_CODE_${codeTokens.length}@@`;
    codeTokens.push(`<code>${code}</code>`);
    return token;
  });

  rendered = rendered
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_([^_]+)_/g, "$1<em>$2</em>");

  return rendered.replace(/@@INLINE_CODE_(\d+)@@/g, (_match, index) => codeTokens[Number(index)]);
}

function isListItem(line) {
  return /^\s*(?:[-+*]|•)\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line);
}

function isBlockStart(lines, index) {
  const line = lines[index] || "";
  return !line.trim() || /^```/.test(line) || /^#{1,3}\s+/.test(line) ||
    /^\s*(?:[-+*]|•)\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line) ||
    /^>\s?/.test(line) || /^([-*_])\1\1+\s*$/.test(line) ||
    (line.includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] || ""));
}

function renderList(lines, ordered) {
  const itemPattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*(?:[-+*]|•)\s+/;
  const tag = ordered ? "ol" : "ul";
  const items = lines.map(line => `<li>${renderInline(line.replace(itemPattern, ""))}</li>`).join("");
  return `<${tag}>${items}</${tag}>`;
}

function renderTable(headerLine, separatorLine, rows) {
  const cells = line => line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
  const headers = cells(headerLine);
  const bodyRows = rows.map(row => cells(row));
  const headerHtml = headers.map(header => `<th>${renderInline(header)}</th>`).join("");
  const bodyHtml = bodyRows.map(row => `<tr>${headers.map((_, index) => `<td>${renderInline(row[index] || "")}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function renderCodeBlock(language, code) {
  const label = language || "code";
  return `<section class="code-block"><header><span>${escapeHtml(label)}</span><button class="copy-code" type="button">Copy</button></header><pre><code>${escapeHtml(code)}</code></pre></section>`;
}

function renderMarkdown(source) {
  const lines = normalizeEncoding(source).replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const codeFence = line.match(/^```\s*([^\s`]*)\s*$/);
    if (codeFence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(renderCodeBlock(codeFence[1], codeLines.join("\n")));
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^([-*_])\1\1+\s*$/.test(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    if (line.includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] || "")) {
      const headerLine = line;
      const separatorLine = lines[index + 1];
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(lines[index]);
        index += 1;
      }
      blocks.push(renderTable(headerLine, separatorLine, rows));
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${quoteLines.map(renderInline).join("<br>")}</blockquote>`);
      continue;
    }

    if (isListItem(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const listLines = [];
      while (index < lines.length && lines[index].trim() && isListItem(lines[index]) === true && /^\s*\d+[.)]\s+/.test(lines[index]) === ordered) {
        listLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderList(listLines, ordered));
      continue;
    }

    const paragraph = [];
    while (index < lines.length && lines[index].trim() && (paragraph.length === 0 || !isBlockStart(lines, index))) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  }

  return blocks.join("");
}

function attachCodeActions(container) {
  container.querySelectorAll(".copy-code").forEach(button => {
    button.addEventListener("click", async () => {
      const code = button.closest(".code-block").querySelector("code").textContent;
      try {
        await navigator.clipboard.writeText(code);
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = "Copy"; }, 1500);
      } catch {
        button.textContent = "Select code";
      }
    });
  });
}

function updateComposerState() {
  sendButton.disabled = requestInProgress || !promptInput.value.trim();
  connection.classList.toggle("busy", requestInProgress);
  connectionText.textContent = requestInProgress ? "Agent working" : "Ready";
}

function resizePrompt() {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 180)}px`;
}

function scrollToLatest() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function removeWelcome() {
  document.getElementById("welcome")?.remove();
}

function createMessage(role, content = "") {
  removeWelcome();
  const message = document.createElement("article");
  message.className = `message ${role}`;
  const icon = document.createElement("div");
  icon.className = "message-icon";
  icon.textContent = role === "user" ? "You" : "✦";

  const contentWrapper = document.createElement("div");
  contentWrapper.className = "message-content";
  const metadata = document.createElement("div");
  metadata.className = "message-meta";
  metadata.innerHTML = role === "user"
    ? "<strong>You</strong><span>Request</span>"
    : "<strong>Agent</strong><span>Repository analysis</span>";
  const body = document.createElement("div");
  body.className = "message-body";

  if (role === "user") {
    body.textContent = content;
  } else {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.textContent = "Copy response";
    copyButton.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(body.dataset.source || body.textContent || "");
        copyButton.textContent = "Copied";
        setTimeout(() => { copyButton.textContent = "Copy response"; }, 1500);
      } catch {
        copyButton.textContent = "Copy unavailable";
      }
    });
    actions.append(copyButton);
    contentWrapper.append(metadata, body, actions);
  }

  if (role === "user") contentWrapper.append(metadata, body);
  message.append(icon, contentWrapper);
  conversation.append(message);
  scrollToLatest();
  return body;
}

function ensureThinking() {
  if (thinkingIndicator) return thinkingIndicator;
  thinkingIndicator = document.createElement("div");
  thinkingIndicator.className = "thinking";
  thinkingIndicator.innerHTML = "<i></i><i></i><i></i><span>Agent is thinking</span>";
  conversation.append(thinkingIndicator);
  return thinkingIndicator;
}

function removeThinking() {
  thinkingIndicator?.remove();
  thinkingIndicator = null;
}

function createToolCard(event) {
  removeThinking();
  const card = document.createElement("button");
  card.type = "button";
  card.className = "tool-card";
  const header = document.createElement("div");
  header.className = "tool-head";
  header.innerHTML = `<span>⌘ ${escapeHtml(event.name)}</span><span class="running">Running…</span>`;
  const details = document.createElement("pre");
  details.className = "tool-details";
  details.textContent = JSON.stringify(event.arguments || {}, null, 2);
  card.append(header, details);
  card.addEventListener("click", () => card.classList.toggle("expanded"));
  conversation.append(card);
  toolCards.set(event.tool_id, card);
  scrollToLatest();
}

function completeToolCard(event) {
  const card = toolCards.get(event.tool_id);
  if (!card) return;
  const status = card.querySelector(".tool-head span:last-child");
  status.className = event.success ? "complete" : "failed";
  status.textContent = `${event.success ? "Completed" : "Failed"} · ${Number(event.elapsed || 0).toFixed(2)}s`;
  card.querySelector(".tool-details").textContent += `\n\n${event.result || "No output"}`;
}

function finishActiveAssistant(shouldSpeak = false) {
  if (!activeAssistantBody) return;
  const source = activeAssistantBody.dataset.source || "";
  activeAssistantBody.innerHTML = renderMarkdown(source);
  attachCodeActions(activeAssistantBody);
  activeAssistantBody = null;
  completedResponses += 1;
  responseCount.textContent = `${completedResponses} response${completedResponses === 1 ? "" : "s"}`;

  if (shouldSpeak) speakResponse(source);
}

function processEvent(type, data) {
  if (type === "completion_start") {
    finishActiveAssistant();
    ensureThinking();
  } else if (type === "status") {
    ensureThinking().querySelector("span").textContent = data.message || "Agent is thinking";
  } else if (type === "delta") {
    removeThinking();
    if (!activeAssistantBody) {
      activeAssistantBody = createMessage("assistant");
      activeAssistantBody.dataset.source = "";
    }
    activeAssistantBody.dataset.source += data.text || "";
    activeAssistantBody.textContent = activeAssistantBody.dataset.source;
    scrollToLatest();
  } else if (type === "tool_start") {
    createToolCard(data);
  } else if (type === "tool_result") {
    completeToolCard(data);
  } else if (type === "error") {
    throw new Error(data.message || "The agent request failed.");
  }
}

async function readEventStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop();

      for (const frame of frames) {
        const lines = frame.split(/\r?\n/);
        const eventLine = lines.find(line => line.startsWith("event:"));
        const dataLines = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart());
        if (!eventLine || dataLines.length === 0) continue;
        const eventType = eventLine.slice(6).trim();
        const data = JSON.parse(dataLines.join("\n"));
        if (eventType === "done") return;
        processEvent(eventType, data);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function submitMessage(value) {
  const message = value.trim();
  if (!message || requestInProgress) return;

  createMessage("user", message);
  promptInput.value = "";
  resizePrompt();
  requestInProgress = true;
  updateComposerState();
  ensureThinking();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (!response.ok || !response.body) throw new Error((await response.text()) || "Unable to start the agent request.");
    await readEventStream(response);
    finishActiveAssistant(true);
  } catch (error) {
    finishActiveAssistant();
    const errorBody = createMessage("assistant");
    errorBody.textContent = `I couldn’t complete that request: ${error.message}`;
  } finally {
    removeThinking();
    requestInProgress = false;
    updateComposerState();
    promptInput.focus();
  }
}

composer.addEventListener("submit", event => {
  event.preventDefault();
  submitMessage(promptInput.value);
});
promptInput.addEventListener("input", () => { resizePrompt(); updateComposerState(); });
promptInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submitMessage(promptInput.value);
  }
});
document.querySelectorAll(".suggestion").forEach(button => {
  button.addEventListener("click", () => submitMessage(button.querySelector("strong").textContent));
});
document.getElementById("scroll-latest").addEventListener("click", scrollToLatest);
microphoneButton.addEventListener("click", startVoiceInput);
voiceOutputButton.addEventListener("click", () => {
  voiceOutputEnabled = !voiceOutputEnabled;
  if (!voiceOutputEnabled) window.speechSynthesis?.cancel();
  updateVoiceOutputButton();
  setVoiceStatus(
    "Voice assistant",
    voiceOutputEnabled ? "Voice replies are enabled." : "Voice replies are muted.",
  );
});
document.getElementById("new-chat").addEventListener("click", async () => {
  if (requestInProgress) return;
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  sessionStorage.removeItem(sessionKey);
  window.location.reload();
});
fetch("/api/info")
  .then(response => response.json())
  .then(info => {
    document.getElementById("repository").textContent = info.repository;
    document.getElementById("model").textContent = `${info.model} · OpenRouter`;
  })
  .catch(() => { document.getElementById("repository").textContent = "Workspace unavailable"; });

if (!SpeechRecognitionApi) {
  microphoneButton.disabled = true;
  microphoneButton.title = "Speech recognition is not supported by this browser";
  setVoiceStatus("Voice unavailable", "Use Chrome, Edge, or Safari to speak with the agent.", "error");
} else {
  setVoiceStatus("Voice assistant", "Press the mic to speak your request.");
}
updateVoiceOutputButton();
resizePrompt();
updateComposerState();
