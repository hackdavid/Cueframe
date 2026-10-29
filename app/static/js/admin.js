(() => {
  let tab = "usage";
  const form = document.getElementById("filters");
  const cols = {
    usage: ["created_at", "username", "user_id", "session_id", "category", "kind", "plan", "status", "job_id"],
    logs: ["ts", "level", "logger", "message", "user_id", "session_id", "job_id", "fields"],
    users: ["username", "id", "plan", "role", "auth_provider", "used", "created_at"],
  };
  const cell = (v) => {
    const td = document.createElement("td");
    td.className = "whitespace-nowrap px-3 py-2 align-top text-zinc-300";
    const text = v && typeof v === "object" ? JSON.stringify(v) : v ?? "";
    td.textContent = String(text).slice(0, 300);
    td.title = String(text);
    if (/^(usr|ses|j)_/.test(text)) {
      td.className += " cursor-pointer text-sky-400 hover:underline";
      const name = text.startsWith("usr") ? "user_id" : text.startsWith("ses") ? "session_id" : "job_id";
      td.addEventListener("click", () => { form.elements[name].value = text; load(); });
    }
    return td;
  };
  async function load() {
    const params = new URLSearchParams();
    for (const [k, v] of new FormData(form)) if (v && (tab === "logs" || !["q", "level"].includes(k))) params.set(k, v);
    const path = tab === "users" ? "/api/admin/users" : `/api/admin/${tab}?${params}`;
    const res = await fetch(path, { credentials: "same-origin" });
    const data = await res.json();
    let rows = tab === "usage" ? data.records : data;
    if (tab === "users") rows = rows.map((u) => ({ ...u, used: `${u.usage.used.total} videos, ${u.usage.used.edit} edits` }));
    document.getElementById("totals").replaceChildren(
      ...Object.entries(tab === "usage" ? data.totals : {}).map(([cat, by]) => {
        const s = document.createElement("span");
        s.className = "rounded-full border border-white/10 px-3 py-1 text-zinc-300";
        s.textContent = `${cat}: ` + Object.entries(by).map(([k, n]) => `${n} ${k}`).join(", ");
        return s;
      }),
    );
    const head = document.createElement("tr");
    for (const c of cols[tab]) { const th = document.createElement("th"); th.className = "px-3 py-2 font-medium"; th.textContent = c; head.append(th); }
    document.getElementById("head").replaceChildren(head);
    document.getElementById("rows").replaceChildren(...(Array.isArray(rows) ? rows : []).map((r) => {
      const tr = document.createElement("tr");
      if (r.levelno >= 30) tr.className = "bg-amber-500/5";
      for (const c of cols[tab]) tr.append(cell(r[c]));
      return tr;
    }));
  }
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach((x) => {
      x.classList.toggle("border-sky-500/60", x === b); x.classList.toggle("bg-sky-500/10", x === b); x.classList.toggle("border-white/10", x !== b);
    });
    load();
  }));
  form.addEventListener("submit", (e) => { e.preventDefault(); load(); });
  load();
})();
