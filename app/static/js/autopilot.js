// Daily autopilot review page: lists runs, starts today's videos, approves/rejects.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const BADGE = {
    running: "bg-amber-500/15 text-amber-300", queued: "bg-amber-500/15 text-amber-300",
    ready: "bg-emerald-500/15 text-emerald-300", failed: "bg-rose-500/15 text-rose-300",
  };
  const REVIEW = { approved: "text-emerald-300", rejected: "text-rose-300", pending: "text-zinc-400" };

  async function api(path, opts = {}) {
    const res = await fetch(`/api${path}`, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
    if (res.status === 401) { location.href = "/login?next=/autopilot"; return null; }
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  }

  function card(r) {
    const tags = (r.hashtags || []).join(" ");
    const media = r.status === "ready"
      ? `<video class="aspect-video w-full bg-black" controls preload="metadata" src="${esc(r.video_url)}" poster="${esc(r.thumbnail_url)}"></video>`
      : `<div class="grid aspect-video w-full place-items-center bg-zinc-900 px-6 text-center text-sm text-zinc-400">${
          r.status === "failed" ? `Failed: ${esc(r.error)}` : "Making the video… this takes a few minutes."}</div>`;
    const review = r.status !== "ready" ? "" : `
      <label class="mt-3 block text-xs font-medium uppercase tracking-wider text-zinc-500">Caption</label>
      <textarea data-caption="${r.id}" rows="5" class="mt-1 w-full rounded-lg border border-white/10 bg-zinc-950 p-2 text-sm">${esc(r.caption)}</textarea>
      <label class="mt-2 block text-xs font-medium uppercase tracking-wider text-zinc-500">Hashtags</label>
      <input data-tags="${r.id}" class="mt-1 w-full rounded-lg border border-white/10 bg-zinc-950 p-2 text-sm" value="${esc(tags)}">
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <button data-review="approved" data-id="${r.id}" class="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500">Approve</button>
        <button data-review="rejected" data-id="${r.id}" class="rounded-lg border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5">Reject</button>
        <button data-copy="${r.id}" class="rounded-lg border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5">Copy caption + hashtags</button>
        <a href="${esc(r.video_url)}" download class="text-sm text-sky-400 hover:text-sky-300">Download</a>
        <span class="ml-auto text-sm ${REVIEW[r.review] || ""}">${esc(r.review)}</span>
      </div>`;
    return `<article class="overflow-hidden rounded-xl border border-white/10 bg-zinc-900/60">
      ${media}
      <div class="p-4">
        <div class="flex items-center gap-2 text-xs">
          <span class="rounded-full px-2 py-0.5 font-medium ${BADGE[r.status] || ""}">${esc(r.status)}</span>
          <span class="text-zinc-500">${esc(r.day)} · trending ${esc(r.topic)}</span>
          ${r.conversation_id ? `<a class="ml-auto text-sky-400 hover:text-sky-300" href="/dashboard/c/${esc(r.conversation_id)}">Open chat</a>` : ""}
        </div>
        <h2 class="mt-2 font-semibold">${esc(r.title || r.item || r.request)}</h2>
        ${review}
      </div>
    </article>`;
  }

  async function load() {
    const data = await api("/autopilot");
    if (!data) return;
    $("ap-when").textContent = data.at
      ? `Every day at ${data.at} UTC Cueframe makes an influencer video about the top trending GitHub repo and AI paper. Review them here before posting.`
      : "The daily schedule is off (set CUEFRAME_AUTOPILOT_AT, e.g. 07:00). You can still make today's videos now.";
    $("ap-list").innerHTML = data.runs.map(card).join("");
    $("ap-empty").classList.toggle("hidden", data.runs.length > 0);
    const busy = data.runs.some((r) => r.status === "running" || r.status === "queued");
    $("ap-run").disabled = busy;
    clearTimeout(load.t);
    if (busy) load.t = setTimeout(load, 10000);
  }

  function note(text) { $("ap-note").textContent = text; $("ap-note").classList.remove("hidden"); }

  $("ap-run").addEventListener("click", async () => {
    $("ap-run").disabled = true;
    try { const r = await api("/autopilot/run", { method: "POST", body: "{}" }); note(`Started ${r.runs.length} video(s).`); }
    catch (e) { note(e.message); }
    load();
  });

  $("ap-list").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    const id = b.dataset.id || b.dataset.copy;
    const caption = document.querySelector(`[data-caption="${id}"]`)?.value ?? "";
    const hashtags = (document.querySelector(`[data-tags="${id}"]`)?.value ?? "").split(/\s+/).filter(Boolean);
    if (b.dataset.copy) {
      await navigator.clipboard.writeText(`${caption}\n\n${hashtags.join(" ")}`);
      note("Caption and hashtags copied.");
      return;
    }
    try { await api(`/autopilot/${id}`, { method: "PATCH", body: JSON.stringify({ review: b.dataset.review, caption, hashtags }) }); }
    catch (e) { note(e.message); }
    load();
  });

  load().catch((e) => note(e.message));
})();
