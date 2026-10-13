const urlInput = document.getElementById("url");
const modeSelect = document.getElementById("mode");
const qualitySelect = document.getElementById("quality");
const goBtn = document.getElementById("goBtn");
const statusBox = document.getElementById("statusBox");
const linksBox = document.getElementById("linksBox");
const errorBox = document.getElementById("errorBox");

let pollInterval = null;

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.remove("hidden");
}

function clearAll() {
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
  statusBox.classList.add("hidden");
  statusBox.textContent = "";
  linksBox.classList.add("hidden");
  linksBox.innerHTML = "";
}

goBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return showError("Please enter a URL");

  clearAll();
  goBtn.disabled = true;
  statusBox.textContent = "⏳ Extracting download links...";
  statusBox.classList.remove("hidden");

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        mode: modeSelect.value,
        quality: qualitySelect.value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to start");
    pollStatus(data.task_id);
  } catch (e) {
    showError(e.message);
    goBtn.disabled = false;
    statusBox.classList.add("hidden");
  }
});

function pollStatus(taskId) {
  if (pollInterval) clearInterval(pollInterval);

  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${taskId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Status error");

      if (data.status === "running") {
        statusBox.textContent = "⏳ Extracting links...";
      } else if (data.status === "done") {
        clearInterval(pollInterval);
        goBtn.disabled = false;
        statusBox.classList.add("hidden");
        renderLinks(data.links || []);
      } else if (data.status === "error") {
        clearInterval(pollInterval);
        goBtn.disabled = false;
        statusBox.classList.add("hidden");
        showError(data.error || "Extraction failed");
      }
    } catch (e) {
      clearInterval(pollInterval);
      goBtn.disabled = false;
      showError(e.message);
    }
  }, 1000);
}

function renderLinks(links) {
  if (!links || !links.length) {
    linksBox.innerHTML = "<em>No links found.</em>";
    linksBox.classList.remove("hidden");
    return;
  }

  linksBox.innerHTML = "";

  // Header + "Copy all" button
  const header = document.createElement("div");
  header.className = "links-header";

  const headerText = document.createElement("span");
  headerText.textContent = `📦 ${links.length} link(s) ready`;
  header.appendChild(headerText);

  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-all-btn";
  copyBtn.textContent = "📋 Copy all links";
  header.appendChild(copyBtn);
  linksBox.appendChild(header);

  // Textarea with all URLs, one per line
  const textarea = document.createElement("textarea");
  textarea.className = "links-textarea";
  textarea.readOnly = true;
  textarea.rows = Math.min(links.length + 2, 20);
  textarea.value = links
    .filter((it) => !it.error)
    .map((it) =>
      it.mode === "stream"
        ? `${window.location.origin}/api/stream?video_url=${encodeURIComponent(it.webpage_url)}&quality=${encodeURIComponent(it.quality || "720")}&filename=${encodeURIComponent(it.filename)}`
        : it.url,
    )
    .join("\n");
  linksBox.appendChild(textarea);

  // Copy button behavior
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(textarea.value);
      copyBtn.textContent = "✅ Copied!";
      setTimeout(() => (copyBtn.textContent = "📋 Copy all links"), 2000);
    } catch (e) {
      textarea.select();
      document.execCommand("copy");
      copyBtn.textContent = "✅ Copied!";
      setTimeout(() => (copyBtn.textContent = "📋 Copy all links"), 2000);
    }
  });

  // Individual rows with download buttons (same as before)
  links.forEach((item) => {
    const row = document.createElement("div");
    row.className = "link-row";

    const info = document.createElement("div");
    info.className = "link-info";

    const title = document.createElement("div");
    title.className = "link-title";
    title.textContent = item.title || `Video ${item.index || ""}`;
    info.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "link-meta";
    if (item.error) {
      meta.textContent = "⚠️ " + item.error;
      meta.style.color = "#b30000";
    } else {
      const ext = (item.ext || "mp4").toString().toUpperCase();
      const quality = item.quality ? ` • ${String(item.quality)}` : "";
      const size = item.filesize
        ? ` • ${(item.filesize / 1024 / 1024).toFixed(2)} MB`
        : "";
      meta.textContent = `${ext}${quality}${size}`;
    }
    info.appendChild(meta);
    row.appendChild(info);

    if (!item.error) {
      const btn = document.createElement("a");
      btn.className = "download-btn";
      btn.textContent = "⬇ Download";

      if (item.mode === "stream") {
        btn.href =
          `/api/stream?video_url=${encodeURIComponent(item.webpage_url)}` +
          `&quality=${encodeURIComponent(item.quality || "720")}` +
          `&filename=${encodeURIComponent(item.filename)}`;
      } else {
        btn.href = item.url;
      }
      btn.setAttribute("download", item.filename || "download");
      row.appendChild(btn);
    }

    linksBox.appendChild(row);
  });

  linksBox.classList.remove("hidden");
}
