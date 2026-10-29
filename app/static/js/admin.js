(() => {
  let tab = "stats";
  const form = document.getElementById("filters");
  const cols = {
    usage: ["created_at", "username", "user_id", "session_id", "category", "kind", "plan", "status", "job_id"],
    logs: ["ts", "level", "logger", "message", "user_id", "session_id", "job_id", "fields"],
    users: ["username", "id", "plan", "role", "auth_provider", "used", "bonus_videos", "referred_by", "created_at"],
    stats: ["kind", "succeeded", "failed", "cancelled", "running", "success_rate", "avg_seconds", "p95_seconds", "errors"],
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
    const path = tab === "users" ? "/api/admin/users" : tab === "stats" ? "/api/admin/stats" : `/api/admin/${tab}?${params}`;
    const res = await fetch(path, { credentials: "same-origin" });
    const data = await res.json();
    let rows = tab === "usage" ? data.records : data;
    form.classList.toggle("hidden", tab === "stats" || tab === "users");
    const box = document.getElementById("stats");
    box.replaceChildren();
    if (tab === "stats") {
      const tile = (label, value, warn) => {
        const d = document.createElement("div");
        d.className = `rounded-xl border p-3 ${warn ? "border-amber-500/50 bg-amber-500/10" : "border-white/10 bg-white/5"}`;
        const a = document.createElement("p"); a.className = "text-xs text-zinc-400"; a.textContent = label;
        const b = document.createElement("p"); b.className = "mt-1 text-xl font-semibold"; b.textContent = value;
        d.append(a, b); return d;
      };
      const pct = data.spend_cap_usd ? data.spend_today_usd / data.spend_cap_usd : 0;
      const alerts = data.alerts.length;
      box.append(
        tile("Estimated spend today", `$${data.spend_today_usd.toFixed(2)} / $${data.spend_cap_usd.toFixed(2)}`, pct >= 0.8),
        tile("Sandboxes running", data.sandboxes_active),
        tile("Alerts (24 h)", alerts, alerts > 0),
        tile("Error tracking / webhook", `${data.sentry ? "Sentry on" : "Sentry off"} · ${data.alert_webhook ? "webhook on" : "webhook off"}`),
      );
      rows = Object.entries(data.jobs).map(([kind, k]) => ({ kind, ...k.statuses, success_rate: k.success_rate == null ? "" : `${Math.round(100 * k.success_rate)}%`,
        avg_seconds: k.avg_seconds, p95_seconds: k.p95_seconds, errors: k.errors.map(([e, n]) => `${n}× ${e}`).join(" | ") }));
      if (alerts) rows = rows.concat(data.alerts.map((a) => ({ kind: "ALERT " + a.ts.slice(0, 16), errors: a.message })));
    }
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
