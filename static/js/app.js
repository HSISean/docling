(() => {
  "use strict";

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const fileList = document.getElementById("fileList");
  const fileCount = document.getElementById("fileCount");
  const startBtn = document.getElementById("startBtn");
  const statusText = document.getElementById("statusText");
  const progressOuter = document.getElementById("progressOuter");
  const progressFill = document.getElementById("progressFill");
  const consoleSection = document.getElementById("consoleSection");
  const consoleDot = document.getElementById("consoleDot");
  const logBody = document.getElementById("logBody");
  const resultsSection = document.getElementById("resultsSection");
  const resultsList = document.getElementById("resultsList");
  const downloadAllBtn = document.getElementById("downloadAllBtn");
  const themeToggle = document.getElementById("themeToggle");
  const themeToggleLabel = document.getElementById("themeToggleLabel");
  const styleSegmented = document.getElementById("styleSegmented");

  let files = [];
  let styleValue = "box";
  let polling = null;
  const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");

  function setTheme(theme, persist) {
    document.documentElement.setAttribute("data-theme", theme);
    if (themeToggleLabel) {
      themeToggleLabel.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    }
    if (persist) {
      try {
        localStorage.setItem("docling-theme", theme);
      } catch {
        // Ignore storage failures in restricted browsing contexts.
      }
    }
  }

  function initializeTheme() {
    let stored = null;
    try {
      stored = localStorage.getItem("docling-theme");
    } catch {
      stored = null;
    }

    if (stored === "light" || stored === "dark") {
      setTheme(stored, false);
      return;
    }
    setTheme("light", false);
  }

  initializeTheme();

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const currentTheme = document.documentElement.getAttribute("data-theme") || "light";
      const nextTheme = currentTheme === "dark" ? "light" : "dark";
      setTheme(nextTheme, true);
    });
  }

  if (themeMedia && themeMedia.addEventListener) {
    themeMedia.addEventListener("change", (event) => {
      let stored = null;
      try {
        stored = localStorage.getItem("docling-theme");
      } catch {
        stored = null;
      }
      if (stored === "light" || stored === "dark") {
        return;
      }
      setTheme(event.matches ? "dark" : "light", false);
    });
  }

  // ---------------------------------------------------------- //
  // File selection
  // ---------------------------------------------------------- //
  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function renderFileList() {
    fileList.innerHTML = "";
    files.forEach((file, i) => {
      const li = document.createElement("li");
      li.className = "exhibit";
      li.innerHTML = `
        <span class="exhibit__tag">EX-${String(i + 1).padStart(2, "0")}</span>
        <span class="exhibit__name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
        <span class="exhibit__size">${formatSize(file.size)}</span>
        <button type="button" class="exhibit__remove" data-index="${i}" aria-label="Remove ${escapeHtml(file.name)}">✕</button>
      `;
      fileList.appendChild(li);
    });
    fileCount.textContent = String(files.length);
    startBtn.disabled = files.length === 0;
    if (files.length === 0) {
      statusText.textContent = "Add exhibits and choose what to redact to begin.";
    } else {
      statusText.textContent = `${files.length} file${files.length === 1 ? "" : "s"} ready.`;
    }
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function addFiles(fileArray) {
    for (const f of fileArray) {
      if (!files.some((existing) => existing.name === f.name && existing.size === f.size)) {
        files.push(f);
      }
    }
    renderFileList();
  }

  fileList.addEventListener("click", (e) => {
    const btn = e.target.closest(".exhibit__remove");
    if (!btn) return;
    const idx = Number(btn.dataset.index);
    files.splice(idx, 1);
    renderFileList();
  });

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", () => {
    addFiles(Array.from(fileInput.files));
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("is-dragover");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("is-dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const dropped = Array.from(e.dataTransfer.files || []);
    addFiles(dropped);
  });

  // ---------------------------------------------------------- //
  // Redaction style toggle
  // ---------------------------------------------------------- //
  styleSegmented.addEventListener("click", (e) => {
    const btn = e.target.closest(".segmented__opt");
    if (!btn) return;
    styleSegmented.querySelectorAll(".segmented__opt").forEach((el) => el.classList.remove("is-active"));
    btn.classList.add("is-active");
    styleValue = btn.dataset.value;
  });

  // ---------------------------------------------------------- //
  // Submit
  // ---------------------------------------------------------- //
  startBtn.addEventListener("click", startRedaction);

  async function startRedaction() {
    if (files.length === 0) return;

    const builtin = Array.from(document.querySelectorAll(".detector__input:checked")).map((el) => el.value);
    const keywords = document.getElementById("keywordsInput").value;
    const regex = document.getElementById("regexInput").value;
    const caseSensitive = document.getElementById("caseSensitive").checked;
    const wholeWord = document.getElementById("wholeWord").checked;

    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    builtin.forEach((label) => form.append("builtin", label));
    form.append("keywords", keywords);
    form.append("regex", regex);
    form.append("case_sensitive", caseSensitive ? "true" : "false");
    form.append("whole_word", wholeWord ? "true" : "false");
    form.append("style", styleValue);

    setRunning(true);
    logBody.textContent = "";
    resultsSection.hidden = true;
    resultsList.innerHTML = "";
    consoleSection.hidden = false;
    statusText.textContent = "Uploading files...";

    let resp;
    try {
      resp = await fetch("/api/redact", { method: "POST", body: form });
    } catch (err) {
      failRun(`Network error: ${err.message}`);
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch {
      failRun("Unexpected server response.");
      return;
    }

    if (!resp.ok) {
      failRun(data.error || "Something went wrong starting the job.");
      return;
    }

    statusText.textContent = "Processing...";
    pollJob(data.job_id);
  }

  function setRunning(isRunning) {
    startBtn.disabled = isRunning || files.length === 0;
    progressOuter.hidden = !isRunning;
    if (isRunning) {
      progressFill.style.width = "0%";
    }
  }

  function failRun(message) {
    setRunning(false);
    statusText.textContent = message;
    consoleSection.hidden = false;
    logBody.textContent += `\nERROR: ${message}\n`;
  }

  function pollJob(jobId) {
    if (polling) clearInterval(polling);
    let lastLogLength = 0;

    const tick = async () => {
      let resp;
      try {
        resp = await fetch(`/api/jobs/${jobId}`);
      } catch {
        return; // transient network hiccup, try again next tick
      }
      if (!resp.ok) {
        clearInterval(polling);
        failRun("Lost track of this job (it may have expired).");
        return;
      }
      const job = await resp.json();

      if (job.log && job.log.length > lastLogLength) {
        const newLines = job.log.slice(lastLogLength);
        logBody.textContent += newLines.join("\n") + "\n";
        logBody.scrollTop = logBody.scrollHeight;
        lastLogLength = job.log.length;
      }

      const { current, total } = job.progress || { current: 0, total: 1 };
      const pct = total ? Math.round((current / total) * 100) : 0;
      progressFill.style.width = `${pct}%`;
      statusText.textContent = `Processing ${current}/${total} file${total === 1 ? "" : "s"}...`;

      if (job.status === "done" || job.status === "error") {
        clearInterval(polling);
        consoleDot.style.animation = "none";
        consoleDot.style.background = job.status === "done" ? "var(--seal)" : "var(--stamp)";
        setRunning(false);
        progressOuter.hidden = true;

        if (job.status === "error") {
          statusText.textContent = `Job failed: ${job.error || "unknown error"}`;
        } else {
          const okCount = job.results.filter((r) => r.ok).length;
          const failCount = job.results.length - okCount;
          statusText.textContent = failCount
            ? `Done — ${okCount} succeeded, ${failCount} failed.`
            : `Done — ${okCount} file${okCount === 1 ? "" : "s"} redacted.`;
          renderResults(jobId, job.results, okCount);
        }
      }
    };

    tick();
    polling = setInterval(tick, 1000);
  }

  function renderResults(jobId, results, okCount) {
    resultsSection.hidden = false;
    resultsList.innerHTML = "";

    results.forEach((r) => {
      const li = document.createElement("li");
      li.className = "manifest__item";
      const ok = r.ok;
      li.innerHTML = `
        <span class="manifest__status ${ok ? "manifest__status--ok" : "manifest__status--fail"}"></span>
        <span class="manifest__names">
          <span class="manifest__from">${escapeHtml(r.input)}</span>
          <span class="manifest__to">${ok ? escapeHtml(r.output) : "Failed to redact"}</span>
        </span>
      `;
      resultsList.appendChild(li);
    });

    if (okCount > 0) {
      downloadAllBtn.hidden = false;
      downloadAllBtn.href = `/api/jobs/${jobId}/download-all`;
      downloadAllBtn.setAttribute("download", "redacted_files.zip");
    } else {
      downloadAllBtn.hidden = true;
      downloadAllBtn.removeAttribute("href");
      downloadAllBtn.removeAttribute("download");
    }
  }
})();
