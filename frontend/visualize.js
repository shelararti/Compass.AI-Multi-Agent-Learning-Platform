const el = (id) => document.getElementById(id);

// Same palette as the renderer (tutor_backend/visualizer/render_video.py
// COLORS) so a node's color here matches what it'll look like in the
// rendered video.
const DOMAIN_COLORS = {
  process: "#EF9F27",
  data: "#1D9E75",
  narrative: "#D85A30",
  conceptual: "#7F77DD",
  spatial: "#378ADD",
  scene: "#D4537E",
  formula: "#40C4B4",
  ignore: "#5A5A56",
};

const state = {
  jobId: null,
  pollTimer: null,
  chunks: [],
  selectedIndex: null,
  domains: [],
};

function setStatus(text, cls) {
  const s = el("viz-status");
  s.textContent = text;
  s.className = "viz-status" + (cls ? ` ${cls}` : "");
}

function setFooter(text) {
  el("viz-footer-text").textContent = text;
}

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------
el("viz-upload-btn").addEventListener("click", async () => {
  const file = el("viz-file").files[0];
  if (!file) {
    setStatus("pick a video file first", "is-error");
    return;
  }

  const form = new FormData();
  form.append("file", file);

  el("viz-upload-btn").disabled = true;
  setStatus("uploading…");
  el("viz-progress-fill").style.width = "0%";

  try {
    const res = await fetch("/api/visualize/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed");

    state.jobId = data.job_id;
    el("viz-source-video").src = `/api/visualize/source/${state.jobId}`;
    el("viz-source-video").hidden = false;
    el("viz-body").hidden = false;
    el("viz-output-video").hidden = true;
    el("viz-download-link").hidden = true;

    startPolling();
  } catch (e) {
    setStatus(e.message, "is-error");
  } finally {
    el("viz-upload-btn").disabled = false;
  }
});

// ---------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------
function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  pollOnce();
  state.pollTimer = setInterval(pollOnce, 1500);
}

async function pollOnce() {
  if (!state.jobId) return;
  try {
    const res = await fetch(`/api/visualize/status/${state.jobId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Status check failed");

    state.chunks = data.chunks;
    state.domains = data.domains;

    if (data.status === "transcribing") {
      setStatus(`transcribing & classifying… ${data.chunks.length} chunk(s) so far`);
      el("viz-progress-fill").style.width = "35%";
    } else if (data.status === "analyzed") {
      setStatus(`analyzed — ${data.chunks.length} chunks. Review below, then render.`, "is-done");
      el("viz-progress-fill").style.width = "70%";
      clearInterval(state.pollTimer);
    } else if (data.status === "rendering") {
      setStatus("rendering final video…");
      el("viz-progress-fill").style.width = "85%";
    } else if (data.status === "done") {
      setStatus("done — rendered video ready below", "is-done");
      el("viz-progress-fill").style.width = "100%";
      clearInterval(state.pollTimer);
      const videoUrl = `/api/visualize/video/${state.jobId}`;
      const out = el("viz-output-video");
      out.src = videoUrl;
      out.hidden = false;
      const dl = el("viz-download-link");
      dl.href = videoUrl;
      dl.hidden = false;
    } else if (data.status === "error") {
      setStatus(data.error || "something went wrong", "is-error");
      clearInterval(state.pollTimer);
    }

    renderMindmap();
    if (state.selectedIndex !== null) renderChunkPanel(state.selectedIndex);
  } catch (e) {
    setStatus(e.message, "is-error");
    clearInterval(state.pollTimer);
  }
}

// ---------------------------------------------------------------------
// Mindmap — central "video" hub, chunks fanned out below in time order,
// colored by domain, sized a little by confidence.
// ---------------------------------------------------------------------
function renderMindmap() {
  const svg = el("viz-mindmap-svg");
  const chunks = state.chunks;
  const W = 900, H = 480;
  const hubX = W / 2, hubY = 50;

  const perRow = 9;
  const rowH = 90;
  const colW = 95;

  let parts = [];

  // hub
  parts.push(`<g><circle cx="${hubX}" cy="${hubY}" r="20" fill="#1E2420"/>
    <text x="${hubX}" y="${hubY + 4}" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#EEF1EC">VIDEO</text></g>`);

  chunks.forEach((c, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const rowCount = Math.min(perRow, chunks.length - row * perRow);
    const rowStartX = hubX - ((rowCount - 1) * colW) / 2;
    const x = rowStartX + col * colW;
    const y = 140 + row * rowH;

    const color = DOMAIN_COLORS[c.visual_domain] || DOMAIN_COLORS.ignore;
    const r = 10 + Math.round((c.confidence || 0) * 6);
    const selected = state.selectedIndex === i ? "is-selected" : "";
    const midY = hubY + (y - hubY) * 0.5;

    parts.push(`
      <path d="M ${hubX} ${hubY + 20} C ${hubX} ${midY}, ${x} ${midY}, ${x} ${y - r}"
            fill="none" stroke="#C7CEC2" stroke-width="1.2"/>
      <g class="viz-node ${selected}" data-index="${i}">
        <circle cx="${x}" cy="${y}" r="${r}" fill="${color}"/>
        <text x="${x}" y="${y + r + 12}">${fmtTime(c.start)}</text>
      </g>
    `);
  });

  svg.setAttribute("viewBox", `0 0 ${W} ${Math.max(H, 200 + Math.ceil(chunks.length / perRow) * rowH)}`);
  svg.innerHTML = parts.join("");

  svg.querySelectorAll(".viz-node").forEach((node) => {
    node.addEventListener("click", () => {
      state.selectedIndex = parseInt(node.getAttribute("data-index"), 10);
      renderMindmap();
      renderChunkPanel(state.selectedIndex);
    });
  });

  renderLegend();
}

function renderLegend() {
  const legend = el("viz-legend");
  legend.innerHTML = Object.entries(DOMAIN_COLORS)
    .map(
      ([name, color]) =>
        `<span class="viz-legend-item"><span class="viz-legend-dot" style="background:${color}"></span>${name}</span>`
    )
    .join("");
}

// ---------------------------------------------------------------------
// Chunk detail panel — view + correct a single chunk, jump the source
// preview to that timestamp.
// ---------------------------------------------------------------------
function renderChunkPanel(index) {
  const c = state.chunks[index];
  if (!c) return;

  const keywordsHtml = c.keywords && c.keywords.length
    ? `<div class="viz-chunk-keywords">${c.keywords.join(" · ")}</div>`
    : "";

  const domainOptions = state.domains
    .map((d) => `<option value="${d}" ${d === c.visual_domain ? "selected" : ""}>${d}</option>`)
    .join("");

  el("viz-chunk-panel").innerHTML = `
    <div class="viz-chunk-time">${fmtTime(c.start)}–${fmtTime(c.end)} · confidence ${c.confidence}</div>
    <p class="viz-chunk-transcript">${c.transcript}</p>
    ${keywordsHtml}
    <select id="viz-domain-select">${domainOptions}</select>
    <button id="viz-jump-btn" class="btn" type="button">Jump to this point</button>
    <button id="viz-ignore-btn" class="btn" type="button">Mark as ignore</button>
  `;

  el("viz-domain-select").addEventListener("change", (e) => updateChunk(index, { domain: e.target.value }));
  el("viz-jump-btn").addEventListener("click", () => {
    const video = el("viz-source-video");
    video.currentTime = c.start;
    video.play();
  });
  el("viz-ignore-btn").addEventListener("click", () => updateChunk(index, { ignore: true }));
}

async function updateChunk(index, body) {
  try {
    const res = await fetch(`/api/visualize/chunk/${state.jobId}/${index}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Update failed");
    state.chunks[index] = { ...state.chunks[index], ...data };
    renderMindmap();
    renderChunkPanel(index);
  } catch (e) {
    setFooter(e.message);
  }
}

// ---------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------
el("viz-render-btn").addEventListener("click", async () => {
  if (!state.jobId) return;
  const layout = el("viz-layout").value;
  el("viz-render-btn").disabled = true;
  setStatus("starting render…");
  try {
    const res = await fetch(`/api/visualize/render/${state.jobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Render failed to start");
    startPolling();
  } catch (e) {
    setStatus(e.message, "is-error");
  } finally {
    el("viz-render-btn").disabled = false;
  }
});
