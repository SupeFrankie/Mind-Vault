(() => {
  const root = document.getElementById("groupChat");
  if (!root) return;

  const socketPath = root.dataset.socketPath;
  const userId = Number(root.dataset.userId);
  const timeZone = root.dataset.timezone;
  const list = document.getElementById("chatMessages");
  const empty = document.getElementById("chatEmpty");
  const connection = document.getElementById("chatConnection");
  const sendState = document.getElementById("chatSendState");
  const form = document.getElementById("chatForm");
  const input = document.getElementById("chatInput");
  const sendButton = document.getElementById("chatSend");
  const characterCount = document.getElementById("chatCharacterCount");
  const loadOlderButton = document.getElementById("loadOlderMessages");
  const renderedIds = new Set();
  const pending = new Map();
  let socket = null;
  let reconnectTimer = null;
  let retryDelay = 1000;
  let stopped = false;
  let hasOlderMessages = false;
  let loadingOlderMessages = false;

  function setConnection(label, state) {
    connection.textContent = label;
    connection.dataset.state = state;
    sendButton.disabled = state !== "connected";
    sendState.textContent = state === "connected"
      ? "Connected. Your messages are saved to this group."
      : state === "denied"
        ? "You no longer have access to this group."
        : "Messages will send when the group chat reconnects.";
  }

  function formatTimestamp(value) {
    const date = new Date(value);
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone,
      }).format(date);
    } catch (_) {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    }
  }

  function scrollToLatest() {
    list.scrollTop = list.scrollHeight;
  }

  function removePending(clientId) {
    const node = pending.get(clientId);
    if (node) node.remove();
    pending.delete(clientId);
  }

  function insertById(node, id) {
    const next = [...list.querySelectorAll(".chat-message[data-message-id]")]
      .find((item) => Number(item.dataset.messageId) > Number(id));
    if (next) list.insertBefore(node, next);
    else list.append(node);
  }

  function renderMessage(message, shouldScroll = true) {
    const messageId = Number(message.id);
    if (renderedIds.has(messageId)) {
      if (message.client_id) removePending(message.client_id);
      return;
    }
    if (message.client_id) removePending(message.client_id);
    empty?.remove();

    const item = document.createElement("li");
    item.className = `chat-message${Number(message.sender_id) === userId ? " is-mine" : ""}`;
    item.dataset.messageId = String(messageId);

    const meta = document.createElement("div");
    meta.className = "chat-message-meta";
    const sender = document.createElement("span");
    sender.className = "chat-message-sender";
    sender.textContent = Number(message.sender_id) === userId ? "You" : message.sender;
    const time = document.createElement("time");
    time.dateTime = message.created_at;
    time.textContent = formatTimestamp(message.created_at);
    meta.append(sender, time);

    const body = document.createElement("p");
    body.className = "chat-message-body";
    body.textContent = message.body;
    item.append(meta, body);
    insertById(item, messageId);
    renderedIds.add(messageId);
    if (shouldScroll) scrollToLatest();
    sendState.textContent = Number(message.sender_id) === userId ? "Message sent." : "New group message.";
  }

  function addPending(body, clientId) {
    empty?.remove();
    const item = document.createElement("li");
    item.className = "chat-message is-mine is-pending";
    item.dataset.pendingId = clientId;
    const meta = document.createElement("div");
    meta.className = "chat-message-meta";
    const sender = document.createElement("span");
    sender.className = "chat-message-sender";
    sender.textContent = "You";
    meta.append(sender);
    const text = document.createElement("p");
    text.className = "chat-message-body";
    text.textContent = body;
    const delivery = document.createElement("span");
    delivery.className = "chat-message-delivery";
    delivery.textContent = "Sending…";
    item.append(meta, text, delivery);
    list.append(item);
    pending.set(clientId, item);
    scrollToLatest();
  }

  function transmit(clientId, body) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    try {
      socket.send(JSON.stringify({ type: "message", client_id: clientId, body }));
      const node = pending.get(clientId);
      if (node) node.querySelector(".chat-message-delivery").textContent = "Sending…";
      return true;
    } catch (_) {
      return false;
    }
  }

  function connect() {
    if (stopped) return;
    setConnection("Connecting…", "connecting");
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${scheme}//${window.location.host}${socketPath}`);

    socket.addEventListener("open", () => {
      retryDelay = 1000;
      setConnection("Connected", "connected");
      pending.forEach((node, clientId) => transmit(clientId, node.querySelector(".chat-message-body").textContent));
    });

    socket.addEventListener("message", (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch (_) { return; }
      if (payload.type === "history") {
        payload.messages.forEach(renderMessage);
        hasOlderMessages = Boolean(payload.has_older);
        loadOlderButton.hidden = !hasOlderMessages;
        loadingOlderMessages = false;
        if (!payload.messages.length && list.querySelectorAll(".chat-message").length === 0) {
          empty.textContent = "No messages yet. Start the conversation.";
          list.append(empty);
        }
        scrollToLatest();
      } else if (payload.type === "history_page") {
        const previousHeight = list.scrollHeight;
        payload.messages.forEach((message) => renderMessage(message, false));
        hasOlderMessages = Boolean(payload.has_older);
        loadingOlderMessages = false;
        loadOlderButton.hidden = !hasOlderMessages;
        loadOlderButton.disabled = false;
        loadOlderButton.textContent = "Load older messages";
        list.scrollTop += list.scrollHeight - previousHeight;
      } else if (payload.type === "message") {
        renderMessage(payload.message);
      } else if (payload.type === "error") {
        const node = pending.get(payload.client_id);
        if (node) {
          const delivery = node.querySelector(".chat-message-delivery");
          delivery.textContent = `Not sent · ${payload.error}`;
          pending.delete(payload.client_id);
        } else {
          sendState.textContent = payload.error;
        }
      }
    });

    socket.addEventListener("close", (event) => {
      if (event.code === 4401 || event.code === 4403) {
        stopped = true;
        setConnection(event.code === 4403 ? "Access removed" : "Sign in required", "denied");
        return;
      }
      if (stopped) return;
      setConnection("Reconnecting…", "disconnected");
      pending.forEach((node) => {
        node.querySelector(".chat-message-delivery").textContent = "Connection interrupted · retrying";
      });
      reconnectTimer = window.setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.7, 10000);
    });

    socket.addEventListener("error", () => {
      setConnection("Connection interrupted", "disconnected");
    });
  }

  function submitMessage() {
    const body = input.value.trim();
    if (!body || body.length > 2000) return;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      sendState.textContent = "Not sent yet. The chat is reconnecting; your draft is still here.";
      return;
    }
    const clientId = crypto.randomUUID();
    addPending(body, clientId);
    if (transmit(clientId, body)) input.value = "";
    updateCharacterCount();
  }

  function updateCharacterCount() {
    characterCount.textContent = `${input.value.length} / 2000`;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitMessage();
  });
  loadOlderButton.addEventListener("click", () => {
    const firstMessage = list.querySelector(".chat-message[data-message-id]");
    if (!hasOlderMessages || loadingOlderMessages || !firstMessage || socket?.readyState !== WebSocket.OPEN) return;
    loadingOlderMessages = true;
    loadOlderButton.disabled = true;
    loadOlderButton.textContent = "Loading…";
    socket.send(JSON.stringify({ type: "history", before_id: firstMessage.dataset.messageId }));
    window.setTimeout(() => {
      if (loadingOlderMessages) {
        loadingOlderMessages = false;
        sendState.textContent = "Older messages could not be loaded. Try again.";
      }
      loadOlderButton.disabled = false;
      loadOlderButton.textContent = "Load older messages";
    }, 5000);
  });
  input.addEventListener("input", updateCharacterCount);
  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      submitMessage();
    }
  });
  window.addEventListener("pagehide", () => {
    stopped = true;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    socket?.close();
  });

  connect();
})();
