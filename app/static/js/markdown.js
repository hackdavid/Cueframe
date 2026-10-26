// Markdown → sanitised HTML. Image syntax pointing at a video/audio file
// renders a player: ![clip](/media/x.mp4) → <video controls>.
(function () {
  const VIDEO = /\.(mp4|webm|mov|m4v|ogv)(\?|#|$)/i;
  const AUDIO = /\.(mp3|wav|ogg|m4a|flac)(\?|#|$)/i;
  const YOUTUBE = /^https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]{11})/i;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function mediaHtml(href, alt, title) {
    const t = title ? ` title="${esc(title)}"` : "";
    if (VIDEO.test(href)) return `<video controls preload="metadata" playsinline src="${esc(href)}"${t}></video>`;
    if (AUDIO.test(href)) return `<audio controls preload="metadata" src="${esc(href)}"${t}></audio>`;
    const yt = href.match(YOUTUBE);
    if (yt) return `<iframe class="aspect-video w-full max-w-xl rounded-xl" src="https://www.youtube-nocookie.com/embed/${yt[1]}" allowfullscreen></iframe>`;
    return `<img src="${esc(href)}" alt="${esc(alt)}" loading="lazy"${t}>`;
  }

  marked.use({
    gfm: true,
    breaks: true,
    renderer: {
      image({ href, title, text }) {
        return mediaHtml(href || "", text, title);
      },
    },
  });

  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
    if (node.tagName === "IFRAME" && !/^https:\/\/www\.youtube-nocookie\.com\/embed\//.test(node.getAttribute("src") || "")) {
      node.remove();
    }
  });

  const PURIFY = {
    ADD_TAGS: ["video", "audio", "source", "iframe"],
    ADD_ATTR: ["controls", "preload", "playsinline", "poster", "allowfullscreen", "target", "muted", "loop"],
  };

  function enhance(root) {
    root.querySelectorAll("pre code").forEach((el) => {
      if (window.hljs) hljs.highlightElement(el);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.textContent = "Copy";
      btn.onclick = async () => {
        await navigator.clipboard.writeText(el.innerText);
        btn.textContent = "Copied";
        setTimeout(() => (btn.textContent = "Copy"), 1200);
      };
      el.parentElement.appendChild(btn);
    });
    return root;
  }

  window.Markdown = {
    render(text) {
      return DOMPurify.sanitize(marked.parse(text || ""), PURIFY);
    },
    into(el, text) {
      el.innerHTML = this.render(text);
      return enhance(el);
    },
    mediaHtml,
    esc,
  };
})();
