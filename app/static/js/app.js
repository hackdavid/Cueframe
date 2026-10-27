(function () {
  const $ = (sel) => document.querySelector(sel);
  const esc = Markdown.esc;
  const ACTIVE = new Set(["queued", "running", "waiting_input"]);
  const DONE = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
  const PHASES = ["research", "plan", "execute", "stitch", "done"];

  const state = {
    conversations: [],
    currentId: $("#app").dataset.conversationId || null,
    messageIds: new Set(),
    jobs: new Map(), // jobs of the current conversation
    pending: [], // {key, file, preview, progress, upload, error}
    baseTitle: document.title,
    unseen: 0,
    ws: null,
    connectionId: null,
    lastSeq: 0,
    requests: new Map(),
    nextRequest: 1,
  };

  // ---------- API ----------
  async function api(path, opts = {}) {
    const headers = opts.body ? { "Content-Type": "application/json" } : {};
    if (state.connectionId) headers["X-Connection-Id"] = state.connectionId;
    const res = await fetch(path, {
      ...opts,
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `${res.status} ${res.statusText}`);
    }
    return res.status === 204 ? null : res.json();
  }

  // ---------- Sidebar ----------
  async function loadConversations() {
    state.conversations = await api("/api/conversations");
    renderSidebar();
  }

  function renderSidebar() {
    const list = $("#chat-list");
    if (!state.conversations.length) {
      list.innerHTML = `<p class="px-3 py-2 text-xs text-zinc-500">No chats yet.</p>`;
      return;
    }
    list.innerHTML = state.conversations
      .map((c) => {
        const active = c.id === state.currentId;
        const busy = c.active_jobs > 0;
        return `<a href="/dashboard/c/${c.id}" data-id="${c.id}" class="chat-link group flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${active ? "bg-white/10 text-white" : "text-zinc-300 hover:bg-white/5"}">
          <span class="flex-1 truncate">${esc(c.title)}</span>
          ${busy ? `<span class="h-2 w-2 animate-pulse rounded-full bg-sky-400" title="Running"></span>` : ""}
          ${c.unread ? `<span class="h-2 w-2 rounded-full bg-emerald-400" title="Finished"></span>` : ""}
          <button data-delete="${c.id}" class="hidden rounded px-1 text-zinc-500 hover:text-red-400 group-hover:block" title="Delete">&times;</button>
        </a>`;
      })
      .join("");
  }

  $("#chat-list").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-delete]");
    if (del) {
      e.preventDefault();
      if (!confirm("Delete this chat?")) return;
      await api(`/api/conversations/${del.dataset.delete}`, { method: "DELETE" });
      if (del.dataset.delete === state.currentId) openConversation(null);
      return;
    }
    const link = e.target.closest(".chat-link");
    if (link) {
      e.preventDefault();
      openConversation(link.dataset.id);
      closeDrawers();
    }
  });

  // ---------- Conversation ----------
  async function openConversation(id, { push = true } = {}) {
    state.currentId = id;
    state.messageIds.clear();
    state.jobs.clear();
    $("#thread").innerHTML = "";
    if (push) history.pushState({}, "", id ? `/dashboard/c/${id}` : "/dashboard");
    const conv = state.conversations.find((c) => c.id === id);
    if (conv) conv.unread = false;
    renderSidebar();
    if (!id) {
      $("#chat-title").textContent = "New chat";
      wsRequest({ type: "open", conversation_id: null }).catch(() => {});
      renderThreadVisibility();
      renderRuns();
      renderPendingStatus();
      return;
    }
    // Ask the server (over the socket, recording this connection's open chat)
    // whether a job is still pending here; fall back to REST if offline.
    const data = await wsRequest({ type: "open", conversation_id: id })
      .then((r) => r.conversation)
      .catch(() => api(`/api/conversations/${id}`));
    if (state.currentId !== id) return;
    $("#chat-title").textContent = data.title;
    data.jobs.forEach((j) => state.jobs.set(j.id, j));
    data.messages.forEach(appendMessage);
    renderWorking();
    renderRuns();
    renderPendingStatus();
    renderThreadVisibility();
    scrollToBottom(true);
  }

  function renderThreadVisibility() {
    const has = state.messageIds.size > 0 || [...state.jobs.values()].some((j) => ACTIVE.has(j.status));
    $("#empty").classList.toggle("hidden", has);
    $("#thread").classList.toggle("hidden", !has);
  }

  function attachmentsHtml(atts) {
    if (!atts || !atts.length) return "";
    const items = atts.map((a) => {
      if (a.kind === "image") return `<img src="${esc(a.url)}" alt="${esc(a.filename)}" class="h-32 w-auto max-w-[16rem] cursor-zoom-in rounded-xl border border-white/10 object-cover">`;
      if (a.kind === "video") return `<video src="${esc(a.url)}" controls preload="metadata" playsinline class="h-40 max-w-xs rounded-xl border border-white/10 bg-black"></video>`;
      if (a.kind === "audio") return `<audio src="${esc(a.url)}" controls class="w-64"></audio>`;
      return `<a href="${esc(a.url)}" target="_blank" rel="noopener" class="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs hover:bg-white/10">
        <span class="grid h-8 w-8 place-items-center rounded-lg bg-white/10 text-[10px] uppercase">${esc((a.filename.split(".").pop() || "file").slice(0, 4))}</span>
        <span class="max-w-[10rem] truncate">${esc(a.filename)}</span><span class="text-zinc-500">${formatSize(a.size)}</span></a>`;
    });
    return `<div class="flex flex-wrap justify-end gap-2">${items.join("")}</div>`;
  }

  function appendMessage(m) {
    if (state.messageIds.has(m.id)) return;
    state.messageIds.add(m.id);
    const el = document.createElement("div");
    el.dataset.messageId = m.id;
    if (m.role === "user") {
      el.className = "flex flex-col items-end gap-2";
      el.innerHTML = `${attachmentsHtml(m.attachments)}${m.content.trim() ? `<div class="md max-w-[85%] rounded-2xl bg-zinc-800 px-4 py-2.5 text-sm"></div>` : ""}`;
      const body = el.querySelector(".md");
      if (body) Markdown.into(body, m.content);
    } else {
      el.className = "flex gap-3";
      el.innerHTML = `<div class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-fuchsia-500 to-sky-500 text-xs font-bold">S</div>
        <div class="md min-w-0 flex-1 text-sm"></div>`;
      Markdown.into(el.querySelector(".md"), m.content);
    }
    const working = $("#working");
    $("#thread").insertBefore(el, working);
    renderThreadVisibility();
  }

  function renderWorking() {
    let el = $("#working");
    const active = [...state.jobs.values()].filter((j) => ACTIVE.has(j.status));
    if (!active.length) {
      el?.remove();
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.id = "working";
      $("#thread").appendChild(el);
    }
    el.className = "space-y-3";
    el.innerHTML = active
      .map((j) => {
        const pct = Math.round((j.progress || 0) * 100);
        const label = j.status === "queued" ? "Queued — waiting for a free worker" : j.label || "Working";
        const ask = j.status === "waiting_input" && j.pending_input;
        if (ask) {
          const btns = ask.options.map((o) => `<button data-answer="${esc(j.id)}" data-input="${esc(ask.input_id)}" data-value="${esc(o)}" class="rounded-lg border border-sky-400/40 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-200 hover:bg-sky-500/25">${esc(o)}</button>`).join("");
          return `<div class="rounded-xl border border-sky-400/30 bg-sky-500/5 p-3"><p class="text-sm">${esc(ask.question)}</p><div class="mt-2 flex flex-wrap gap-2">${btns}</div></div>`;
        }
        return `<div class="flex gap-3">
          <div class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-fuchsia-500 to-sky-500 text-xs font-bold">S</div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 text-sm">
              <span class="bg-gradient-to-r from-zinc-400 via-white to-zinc-400 bg-[length:200%_100%] bg-clip-text text-transparent animate-shimmer">${esc(label)}</span>
              <span class="text-xs text-zinc-500">${pct}%</span>
            </div>
            <div class="mt-2 h-1 w-64 max-w-full overflow-hidden rounded-full bg-white/10"><div class="h-full rounded-full bg-sky-500 transition-all duration-500" style="width:${pct}%"></div></div>
            <p class="mt-1.5 text-xs text-zinc-500">Running in the background — you can leave this chat; you'll be notified when it finishes.</p>
          </div></div>`;
      })
      .join("");
    renderThreadVisibility();
  }

  function renderPendingStatus() {
    const el = $("#chat-status");
    const active = [...state.jobs.values()].filter((j) => ACTIVE.has(j.status));
    if (!state.currentId) {
      el.textContent = "";
      return;
    }
    el.innerHTML = active.length
      ? `<span class="text-sky-400">● ${active.length} job${active.length > 1 ? "s" : ""} pending</span>`
      : `<span class="text-zinc-500">No pending job — send a message to start one</span>`;
  }

  // ---------- Run panel ----------
  const STATUS_STYLE = {
    queued: "bg-zinc-700 text-zinc-200",
    running: "bg-sky-500/20 text-sky-300",
    waiting_input: "bg-amber-500/20 text-amber-300",
    succeeded: "bg-emerald-500/20 text-emerald-300",
    failed: "bg-red-500/20 text-red-300",
    cancelled: "bg-amber-500/20 text-amber-300",
    interrupted: "bg-amber-500/20 text-amber-300",
  };

  function renderRuns() {
    const jobs = [...state.jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    const activeCount = jobs.filter((j) => ACTIVE.has(j.status)).length;
    const badge = $("#runs-badge");
    badge.textContent = activeCount;
    badge.classList.toggle("hidden", !activeCount);
    if (!jobs.length) {
      $("#run-list").innerHTML = `<p class="px-1 text-xs text-zinc-500">Background jobs for this chat show up here.</p>`;
      renderLive();
      return;
    }
    $("#run-list").innerHTML = jobs
      .map((j) => {
        const pct = Math.round((j.progress || 0) * 100);
        const phaseIdx = PHASES.indexOf(j.phase);
        const steps = PHASES.map((p, i) => {
          const cls = j.status === "succeeded" || i < phaseIdx ? "bg-emerald-400" : i === phaseIdx && ACTIVE.has(j.status) ? "bg-sky-400 animate-pulse" : "bg-white/15";
          return `<div class="flex-1"><div class="h-1 rounded-full ${cls}"></div><div class="mt-1 text-[10px] capitalize text-zinc-500">${p}</div></div>`;
        }).join("");
        return `<div class="rounded-xl border border-white/10 bg-zinc-950/60 p-3">
          <div class="flex items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${STATUS_STYLE[j.status] || ""}">${j.status}</span>
            <span class="flex-1 truncate font-mono text-[11px] text-zinc-500">${esc(j.kind)}</span>
            ${ACTIVE.has(j.status) ? `<button data-cancel="${j.id}" class="text-[11px] text-zinc-400 hover:text-red-400">Cancel</button>` : ""}
          </div>
          <p class="mt-2 truncate text-xs text-zinc-300">${esc(j.error || j.label || "Waiting…")}</p>
          <div class="mt-2 flex gap-1">${steps}</div>
          <div class="mt-2 flex justify-between text-[10px] text-zinc-500"><span>${pct}%</span><span>${duration(j)}</span></div>
        </div>`;
      })
      .join("");
    renderLive();
  }

  $("#thread").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-answer]");
    if (!btn) return;
    btn.parentElement.querySelectorAll("button").forEach((b) => (b.disabled = true));
    await api(`/api/jobs/${btn.dataset.answer}/answer`, { method: "POST", body: { input_id: btn.dataset.input, value: btn.dataset.value } });
  });

  // The live desktop lives outside #run-list so the 1 s run-panel refresh never reloads it.
  function renderLive() {
    const box = $("#live-view");
    const j = [...state.jobs.values()].find((x) => x.live_url && ACTIVE.has(x.status));
    const src = j ? j.live_url : "";
    if (box.dataset.src === src) return;
    box.dataset.src = src;
    box.innerHTML = src
      ? `<p class="mb-1 text-[11px] text-zinc-400">Live sandbox (view-only)</p><iframe src="${esc(src)}" title="Live sandbox" class="aspect-video w-full rounded-lg border border-white/10 bg-black"></iframe><a href="${esc(src)}" target="_blank" rel="noopener" class="mt-1 block text-[10px] text-sky-400">Open live view</a>`
      : "";
  }

  $("#run-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-cancel]");
    if (btn) await api(`/api/jobs/${btn.dataset.cancel}/cancel`, { method: "POST" });
  });

  function duration(j) {
    if (!j.started_at) return "";
    const end = j.finished_at ? new Date(j.finished_at) : new Date();
    const s = Math.max(0, Math.round((end - new Date(j.started_at)) / 1000));
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }
  setInterval(() => [...state.jobs.values()].some((j) => ACTIVE.has(j.status)) && renderRuns(), 1000);

  // ---------- Events (WebSocket) ----------
  let retry = 0;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const after = state.lastSeq ? `&after=${state.lastSeq}` : "";
    const ws = new WebSocket(`${proto}://${location.host}/api/ws?client=ui${after}`);
    state.ws = ws;
    ws.onmessage = (e) => {
      const frame = JSON.parse(e.data);
      if (frame.type === "hello") {
        retry = 0;
        state.connectionId = frame.connection_id;
        if (!state.lastSeq) state.lastSeq = frame.last_seq;
        setConn(true);
        if (state.currentId !== undefined) wsRequest({ type: "open", conversation_id: state.currentId }).catch(() => {});
        return;
      }
      if (frame.type === "event") {
        state.lastSeq = Math.max(state.lastSeq, frame.event.seq);
        handleEvent(frame.event);
        return;
      }
      const pending = frame.request_id && state.requests.get(frame.request_id);
      if (pending) {
        state.requests.delete(frame.request_id);
        frame.type === "error" ? pending.reject(new Error(frame.detail)) : pending.resolve(frame);
      }
    };
    ws.onclose = (e) => {
      setConn(false);
      state.connectionId = null;
      state.requests.forEach((p) => p.reject(new Error("socket closed")));
      state.requests.clear();
      if (e.code === 4401) return location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
      setTimeout(connect, Math.min(10000, 500 * 2 ** retry++));
    };
  }

  function wsRequest(frame) {
    const ws = state.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || !state.connectionId) return Promise.reject(new Error("offline"));
    const request_id = `r${state.nextRequest++}`;
    return new Promise((resolve, reject) => {
      state.requests.set(request_id, { resolve, reject });
      ws.send(JSON.stringify({ ...frame, request_id }));
      setTimeout(() => state.requests.delete(request_id) && reject(new Error("timeout")), 10000);
    });
  }
  setInterval(() => wsRequest({ type: "ping" }).catch(() => {}), 25000);

  function setConn(ok) {
    $("#conn-dot").className = `mr-1 inline-block h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-amber-400"}`;
    $("#conn-label").textContent = ok ? `Live · ${state.connectionId}` : "Reconnecting…";
  }

  function handleEvent(ev) {
    const p = ev.payload;
    const isCurrent = ev.conversation_id && ev.conversation_id === state.currentId;
    switch (ev.type) {
      case "message.created":
        if (isCurrent) {
          appendMessage(p.message);
          scrollToBottom();
        }
        return;
      case "conversation.created":
      case "conversation.deleted":
        loadConversations();
        return;
      case "conversation.updated": {
        const c = state.conversations.find((x) => x.id === p.conversation.id);
        if (c) c.title = p.conversation.title;
        if (isCurrent) $("#chat-title").textContent = p.conversation.title;
        renderSidebar();
        return;
      }
      case "sandbox.started":
      case "sandbox.output": {
        const j = state.jobs.get(p.job_id);
        if (!j) return;
        if (p.live_url) j.live_url = p.live_url;
        if (p.line) j.label = p.line;
        renderWorking();
        renderRuns();
        return;
      }
      case "job.progress":
      case "phase.changed":
      case "agent.thinking": {
        const j = state.jobs.get(p.job_id);
        if (!j) return;
        if (p.progress !== undefined) j.progress = p.progress;
        if (p.phase) j.phase = p.phase;
        if (p.label) j.label = p.label;
        renderWorking();
        renderRuns();
        return;
      }
      default:
        if (ev.type.startsWith("job.")) onJobChange(ev, p.job, isCurrent);
    }
  }

  function onJobChange(ev, job, isCurrent) {
    if (isCurrent) state.jobs.set(job.id, job);
    const conv = state.conversations.find((c) => c.id === job.conversation_id);
    if (conv) {
      conv.active_jobs = Math.max(0, (conv.active_jobs || 0) + (ev.type === "job.queued" ? 1 : DONE.has(job.status) ? -1 : 0));
      if (DONE.has(job.status) && (!isCurrent || document.hidden)) conv.unread = true;
    }
    renderSidebar();
    if (isCurrent) {
      renderWorking();
      renderRuns();
      renderPendingStatus();
      scrollToBottom();
    }
    if (DONE.has(job.status)) notifyDone(job, conv);
  }

  // ---------- Notifications ----------
  function notifyDone(job, conv) {
    const title = conv ? conv.title : "Chat";
    const ok = job.status === "succeeded";
    const text = ok ? "finished" : job.status;
    toast(`<b>${esc(title)}</b> — job ${text}${job.error ? `: ${esc(job.error)}` : ""}`, ok ? "ok" : "warn", job.conversation_id);
    if (document.hidden) {
      state.unseen += 1;
      document.title = `(${state.unseen}) ${state.baseTitle}`;
    }
    if ("Notification" in window && Notification.permission === "granted" && (document.hidden || job.conversation_id !== state.currentId)) {
      const n = new Notification(`Cueframe: job ${text}`, { body: title, tag: job.id });
      n.onclick = () => {
        window.focus();
        openConversation(job.conversation_id);
      };
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      state.unseen = 0;
      document.title = state.baseTitle;
    }
  });

  function toast(html, kind, conversationId) {
    const el = document.createElement("div");
    el.className = `cursor-pointer rounded-xl border px-4 py-3 text-sm shadow-xl backdrop-blur ${kind === "ok" ? "border-emerald-500/40 bg-emerald-950/80" : kind === "error" ? "border-red-500/40 bg-red-950/80" : "border-amber-500/40 bg-amber-950/80"}`;
    el.innerHTML = html;
    el.onclick = () => {
      if (conversationId && conversationId !== state.currentId) openConversation(conversationId);
      el.remove();
    };
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  // ---------- Composer & uploads ----------
  const input = $("#input");
  const MAX_PREVIEW = 120;

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  }
  input.addEventListener("input", () => {
    autosize();
    updateSend();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("#composer").requestSubmit();
    }
  });

  function addFiles(files) {
    for (const file of files) {
      const item = { key: Math.random().toString(36).slice(2), file, progress: 0, upload: null, error: null };
      if (/^(image|video)\//.test(file.type)) item.preview = URL.createObjectURL(file);
      state.pending.push(item);
      uploadOne(item);
    }
    renderPending();
  }

  function uploadOne(item) {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("files", item.file, item.file.name);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        item.progress = e.loaded / e.total;
        renderPending();
      }
    };
    xhr.onload = () => {
      if (xhr.status === 201) item.upload = JSON.parse(xhr.responseText)[0];
      else item.error = (JSON.parse(xhr.responseText || "{}").detail) || `Upload failed (${xhr.status})`;
      renderPending();
    };
    xhr.onerror = () => {
      item.error = "Upload failed";
      renderPending();
    };
    item.xhr = xhr;
    xhr.open("POST", "/api/uploads");
    xhr.send(form);
  }

  function renderPending() {
    const box = $("#pending");
    box.classList.toggle("hidden", !state.pending.length);
    box.classList.toggle("flex", !!state.pending.length);
    box.innerHTML = state.pending
      .map((p) => {
        const thumb = p.preview
          ? p.file.type.startsWith("video/")
            ? `<video src="${p.preview}" muted class="h-12 w-12 rounded-lg object-cover"></video>`
            : `<img src="${p.preview}" class="h-12 w-12 rounded-lg object-cover" alt="">`
          : `<div class="grid h-12 w-12 place-items-center rounded-lg bg-white/10 text-[10px] uppercase">${esc((p.file.name.split(".").pop() || "file").slice(0, 4))}</div>`;
        const status = p.error
          ? `<span class="text-red-400">${esc(p.error)}</span>`
          : p.upload
            ? `<span class="text-emerald-400">Ready</span>`
            : `<span class="block h-1 w-20 overflow-hidden rounded bg-white/10"><span class="block h-full bg-sky-500" style="width:${Math.round(p.progress * 100)}%"></span></span>`;
        return `<div class="relative flex items-center gap-2 rounded-xl border border-white/10 bg-zinc-900 p-1.5 pr-3 text-xs">
          ${thumb}
          <div class="min-w-0"><div class="max-w-[9rem] truncate">${esc(p.file.name.slice(0, MAX_PREVIEW))}</div><div class="text-zinc-500">${formatSize(p.file.size)} · ${status}</div></div>
          <button type="button" data-remove="${p.key}" class="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center rounded-full bg-zinc-700 text-xs hover:bg-red-500" title="Remove">&times;</button>
        </div>`;
      })
      .join("");
    updateSend();
  }

  $("#pending").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    const i = state.pending.findIndex((p) => p.key === btn.dataset.remove);
    if (i >= 0) {
      const [p] = state.pending.splice(i, 1);
      if (!p.upload && !p.error) p.xhr?.abort();
      if (p.preview) URL.revokeObjectURL(p.preview);
    }
    renderPending();
  });

  function uploadsBusy() {
    return state.pending.some((p) => !p.upload && !p.error);
  }

  function updateSend() {
    const ready = state.pending.some((p) => p.upload);
    $("#send").disabled = uploadsBusy() || (!input.value.trim() && !ready);
  }

  $("#file-input").addEventListener("change", (e) => {
    addFiles(e.target.files);
    e.target.value = "";
  });

  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });

  let dragDepth = 0;
  const overlay = $("#drop-overlay");
  const main = $("#main");
  main.addEventListener("dragenter", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    dragDepth += 1;
    overlay.classList.replace("hidden", "grid");
  });
  main.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) overlay.classList.replace("grid", "hidden");
  });
  main.addEventListener("dragover", (e) => e.preventDefault());
  main.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    overlay.classList.replace("grid", "hidden");
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });

  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#send").disabled) return;
    const content = input.value;
    const ready = state.pending.filter((p) => p.upload);
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
    $("#send").disabled = true;
    try {
      if (!state.currentId) {
        const conv = await api("/api/conversations", { method: "POST", body: {} });
        state.conversations.unshift({ ...conv, active_jobs: 0 });
        await openConversation(conv.id);
      }
      const res = await api(`/api/conversations/${state.currentId}/messages`, {
        method: "POST",
        body: { content, attachment_ids: ready.map((p) => p.upload.id) },
      });
      appendMessage(res.message);
      state.jobs.set(res.job.id, { ...res.job, ...(state.jobs.get(res.job.id) || {}) });
      renderWorking();
      renderRuns();
      renderPendingStatus();
      input.value = "";
      autosize();
      state.pending = state.pending.filter((p) => !p.upload);
      ready.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
      renderPending();
      scrollToBottom(true);
    } catch (err) {
      toast(esc(err.message), "error");
    } finally {
      updateSend();
    }
  });

  // ---------- Misc UI ----------
  function scrollToBottom(force) {
    const box = $("#messages");
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
    if (force || near) requestAnimationFrame(() => (box.scrollTop = box.scrollHeight));
  }

  function formatSize(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  $("#thread").addEventListener("click", (e) => {
    const img = e.target.closest("img");
    if (!img) return;
    $("#lightbox img").src = img.src;
    $("#lightbox").classList.replace("hidden", "grid");
  });
  $("#lightbox").addEventListener("click", () => $("#lightbox").classList.replace("grid", "hidden"));
  document.addEventListener("keydown", (e) => e.key === "Escape" && $("#lightbox").classList.replace("grid", "hidden"));

  function openDrawer(id) {
    $(id).classList.remove("-translate-x-full", "translate-x-full");
    $("#scrim").classList.remove("hidden");
  }
  function closeDrawers() {
    $("#sidebar").classList.add("-translate-x-full");
    $("#runs").classList.add("translate-x-full");
    $("#scrim").classList.add("hidden");
  }
  $("#open-sidebar").onclick = () => openDrawer("#sidebar");
  $("#toggle-runs").onclick = () => openDrawer("#runs");
  $("#close-sidebar").onclick = closeDrawers;
  $("#close-runs").onclick = closeDrawers;
  $("#scrim").onclick = closeDrawers;
  $("#new-chat").onclick = () => {
    openConversation(null);
    closeDrawers();
    input.focus();
  };
  window.addEventListener("popstate", () => {
    const m = location.pathname.match(/^\/dashboard\/c\/([\w-]+)/);
    openConversation(m ? m[1] : null, { push: false });
  });

  // ---------- Boot ----------
  state.currentId = state.currentId || null;
  connect();
  loadConversations().then(() => openConversation(state.currentId, { push: false }));
  updateSend();
})();
