const el = (id) => document.getElementById(id);
const studentId = () => el("student-id").value.trim() || "student1";

const MAX_COMPARE = 5;

const state = {
  meta: { formats: [], tags: [], levels: [], axes: {} },
  subjects: [],
  resources: [],
  shareMode: "upload",
  selectedStars: 0,
  selectedAxisStars: {},
  selectedTags: new Set(),
  compareSelected: new Set(),
};

function setFooter(text) {
  el("res-footer-text").textContent = text;
}

function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString();
}

function starString(avg) {
  if (avg === null || avg === undefined) return "no ratings yet";
  const full = Math.round(avg);
  return "★".repeat(full) + "☆".repeat(5 - full) + ` (${avg})`;
}

// ---------------------------------------------------------------------
// Bootstrap: meta + subjects, then the list
// ---------------------------------------------------------------------
async function bootstrap() {
  const [meta, subjects] = await Promise.all([
    fetch("/api/resources/meta").then((r) => r.json()),
    fetch("/api/subjects").then((r) => r.json()),
  ]);
  state.meta = meta;
  state.subjects = subjects;

  const subjectOptionsHtml = subjects.map((s) => `<option value="${s.id}">${s.title}</option>`).join("");
  el("res-filter-subject").insertAdjacentHTML("beforeend", subjectOptionsHtml);
  el("res-subject").insertAdjacentHTML("beforeend", subjectOptionsHtml);

  const formatOptionsHtml = meta.formats.map((f) => `<option value="${f}">${f}</option>`).join("");
  el("res-filter-format").insertAdjacentHTML("beforeend", formatOptionsHtml);

  const uploadFormats = meta.formats.filter((f) => f !== "youtube" && f !== "link");
  el("res-upload-format").innerHTML = uploadFormats.map((f) => `<option value="${f}">${f}</option>`).join("");
  const linkFormats = ["youtube", "link"];
  el("res-link-format").innerHTML = linkFormats.map((f) => `<option value="${f}">${f}</option>`).join("");

  await loadList();
}

// ---------------------------------------------------------------------
// List
// ---------------------------------------------------------------------
async function loadList() {
  const params = new URLSearchParams();
  const subject = el("res-filter-subject").value;
  const format = el("res-filter-format").value;
  const sort = el("res-filter-sort").value;
  const q = el("res-search").value.trim();
  if (subject) params.set("subject_id", subject);
  if (format) params.set("format", format);
  if (q) params.set("q", q);
  params.set("sort", sort);

  try {
    const res = await fetch(`/api/resources?${params}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't load resources");
    state.resources = data;
    renderList();
  } catch (e) {
    setFooter(e.message);
  }
}

function renderList() {
  const list = el("res-list");
  if (state.resources.length === 0) {
    list.innerHTML = `<p class="hint">No resources yet — be the first to share one.</p>`;
    return;
  }

  list.innerHTML = state.resources
    .map((r) => {
      const subjectTitle = r.subject_id
        ? (state.subjects.find((s) => s.id === r.subject_id) || {}).title || r.subject_id
        : "general";
      const topTags = Object.entries(r.tag_counts || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([t]) => `<span class="res-tag-chip">${t}</span>`)
        .join("");

      return `
        <div class="res-card" data-id="${r.id}">
          <div class="res-card-top">
            <label class="res-compare-check" title="select to compare">
              <input type="checkbox" class="res-compare-checkbox" data-id="${r.id}" ${state.compareSelected.has(r.id) ? "checked" : ""}>
            </label>
            <span class="res-card-title">${r.title}</span>
            <span class="res-card-format">${r.format}</span>
          </div>
          <div class="res-card-desc">${r.description || ""}</div>
          <div class="res-card-meta">
            <span class="res-stars">${starString(r.average_rating)}</span>
            <span>${r.rating_count} rating(s)</span>
            <span>${subjectTitle}</span>
            <span>by ${r.uploaded_by}</span>
            <span>${fmtDate(r.uploaded_at)}</span>
          </div>
          <div style="margin-top:8px">${topTags}</div>
        </div>
      `;
    })
    .join("");

  list.querySelectorAll(".res-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".res-compare-check")) return;
      openDetail(card.getAttribute("data-id"));
    });
  });

  list.querySelectorAll(".res-compare-checkbox").forEach((box) => {
    box.addEventListener("click", (e) => e.stopPropagation());
    box.addEventListener("change", () => toggleCompare(box.getAttribute("data-id"), box));
  });

  renderCompareBar();
}

function toggleCompare(id, checkboxEl) {
  if (state.compareSelected.has(id)) {
    state.compareSelected.delete(id);
  } else {
    if (state.compareSelected.size >= MAX_COMPARE) {
      setFooter(`You can compare up to ${MAX_COMPARE} at a time — unselect one first.`);
      if (checkboxEl) checkboxEl.checked = false;
      return;
    }
    state.compareSelected.add(id);
  }
  renderCompareBar();
}

function renderCompareBar() {
  const bar = el("res-compare-bar");
  const n = state.compareSelected.size;
  bar.hidden = n < 2;
  el("res-compare-count").textContent = `${n} selected`;
}

el("res-compare-clear").addEventListener("click", () => {
  state.compareSelected.clear();
  renderList();
});

el("res-compare-btn").addEventListener("click", openCompare);
el("res-compare-close").addEventListener("click", () => { el("res-compare-overlay").hidden = true; });
el("res-compare-overlay").addEventListener("click", (e) => {
  if (e.target === el("res-compare-overlay")) el("res-compare-overlay").hidden = true;
});

function openCompare() {
  const chosen = state.resources.filter((r) => state.compareSelected.has(r.id));
  if (chosen.length < 2) return;

  const subjectTitle = (r) =>
    r.subject_id ? (state.subjects.find((s) => s.id === r.subject_id) || {}).title || r.subject_id : "general";
  const topTags = (r) =>
    Object.entries(r.tag_counts || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([t, n]) => `${t} (${n})`)
      .join(", ") || "—";
  const openLink = (r) =>
    r.url ? `<a href="${r.url}" target="_blank" rel="noopener">open link</a>` : `<a href="/api/resources/${r.id}/file" target="_blank" rel="noopener">open file</a>`;

  const axisRows = Object.entries(state.meta.axes || {}).map(
    ([key, label]) => [label, (r) => starString((r.axis_averages || {})[key])]
  );

  const rows = [
    ["Format", (r) => r.format],
    ["Subject", (r) => subjectTitle(r)],
    ["Overall rating", (r) => starString(r.average_rating)],
    ...axisRows,
    ["Ratings", (r) => r.rating_count],
    ["Consensus level", (r) => r.consensus_level || "—"],
    ["Top tags", (r) => topTags(r)],
    ["Description", (r) => r.description || "—"],
    ["Shared by", (r) => r.uploaded_by],
    ["Shared on", (r) => fmtDate(r.uploaded_at)],
    ["Link", (r) => openLink(r)],
  ];

  const headerHtml = chosen.map((r) => `<th>${r.title}</th>`).join("");
  const rowsHtml = rows
    .map(
      ([label, get]) =>
        `<tr><th class="res-compare-row-label">${label}</th>${chosen.map((r) => `<td>${get(r)}</td>`).join("")}</tr>`
    )
    .join("");

  el("res-compare-table-wrap").innerHTML = `
    <table class="res-compare-table">
      <thead><tr><th></th>${headerHtml}</tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
  `;
  el("res-detail-overlay").hidden = true;
  el("res-compare-overlay").hidden = false;
}

["res-filter-subject", "res-filter-format", "res-filter-sort"].forEach((id) =>
  el(id).addEventListener("change", loadList)
);

let searchDebounce = null;
el("res-search").addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(loadList, 300);
});

// ---------------------------------------------------------------------
// Share form
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// Mode switch — Find resources vs Share a resource
// ---------------------------------------------------------------------
el("res-mode-find").addEventListener("click", () => {
  el("res-mode-find").classList.add("is-active");
  el("res-mode-share").classList.remove("is-active");
  el("res-find-view").hidden = false;
  el("res-share-view").hidden = true;
});

el("res-mode-share").addEventListener("click", () => {
  el("res-mode-share").classList.add("is-active");
  el("res-mode-find").classList.remove("is-active");
  el("res-share-view").hidden = false;
  el("res-find-view").hidden = true;
});

document.querySelectorAll(".res-share-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".res-share-tab").forEach((t) => t.classList.remove("is-active"));
    tab.classList.add("is-active");
    state.shareMode = tab.getAttribute("data-mode");
    el("res-upload-fields").hidden = state.shareMode !== "upload";
    el("res-link-fields").hidden = state.shareMode !== "link";
  });
});

el("res-submit-btn").addEventListener("click", async () => {
  const title = el("res-title").value.trim();
  if (!title) {
    el("res-share-status").textContent = "give it a title first";
    return;
  }
  const description = el("res-description").value.trim();
  const subjectId = el("res-subject").value || null;

  el("res-submit-btn").disabled = true;
  el("res-share-status").textContent = "sharing…";

  try {
    let res, data;
    if (state.shareMode === "upload") {
      const file = el("res-file").files[0];
      if (!file) throw new Error("pick a file first");
      const form = new FormData();
      form.append("file", file);
      form.append("student_id", studentId());
      form.append("title", title);
      form.append("description", description);
      if (subjectId) form.append("subject_id", subjectId);
      form.append("format", el("res-upload-format").value);
      res = await fetch("/api/resources/upload", { method: "POST", body: form });
    } else {
      const url = el("res-url").value.trim();
      if (!url) throw new Error("paste a url first");
      res = await fetch("/api/resources/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId(), title, description, subject_id: subjectId,
          format: el("res-link-format").value, url,
        }),
      });
    }
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Sharing failed");

    el("res-share-status").textContent = "shared!";
    el("res-title").value = "";
    el("res-description").value = "";
    el("res-url").value = "";
    el("res-file").value = "";
    await loadList();
    setTimeout(() => {
      el("res-mode-find").click();
      el("res-share-status").textContent = "";
    }, 700);
  } catch (e) {
    el("res-share-status").textContent = e.message;
  } finally {
    el("res-submit-btn").disabled = false;
  }
});

// ---------------------------------------------------------------------
// Detail modal — view, rate, classify
// ---------------------------------------------------------------------
async function openDetail(resourceId) {
  const res = await fetch(`/api/resources/${resourceId}`);
  const r = await res.json();
  if (!res.ok) {
    setFooter(r.detail || "Couldn't load resource");
    return;
  }

  state.selectedStars = 0;
  state.selectedAxisStars = {};
  state.selectedTags = new Set();

  const openLink = r.url
    ? `<a class="btn" href="${r.url}" target="_blank" rel="noopener">Open link</a>`
    : `<a class="btn" href="/api/resources/${r.id}/file" target="_blank" rel="noopener">Open file</a>`;

  const reviews = r.ratings
    .slice()
    .reverse()
    .map((rt) => `<div class="res-review-item"><b>${rt.student_id}</b> — ${"★".repeat(rt.stars)}${rt.comment ? ": " + rt.comment : ""}</div>`)
    .join("") || `<p class="hint" style="margin:4px 0">No reviews yet.</p>`;

  const tagCountsHtml = Object.entries(r.tag_counts || {})
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `<span class="res-tag-chip">${t} × ${n}</span>`)
    .join("") || `<p class="hint" style="margin:4px 0">No classifications yet.</p>`;

  const starPicker = [1, 2, 3, 4, 5]
    .map((n) => `<button class="res-star-btn" data-star="${n}" type="button">★</button>`)
    .join("");

  const axisEntries = Object.entries(state.meta.axes || {});
  const axisPickersHtml = axisEntries
    .map(([key, label]) => `
      <div class="res-axis-row" data-axis="${key}">
        <span class="res-axis-label">${label}</span>
        <div class="res-rating-row res-axis-picker" id="res-axis-picker-${key}">
          ${[1, 2, 3, 4, 5].map((n) => `<button class="res-star-btn" data-star="${n}" type="button">★</button>`).join("")}
        </div>
      </div>
    `)
    .join("");

  const axisAveragesHtml = axisEntries
    .map(([key, label]) => `<span>${label}: ${starString((r.axis_averages || {})[key])}</span>`)
    .join("");

  const levelOptions = state.meta.levels.map((l) => `<option value="${l}">${l}</option>`).join("");
  const tagToggles = state.meta.tags
    .map((t) => `<button class="res-tag-toggle" data-tag="${t}" type="button">${t}</button>`)
    .join("");

  el("res-detail-body").innerHTML = `
    <h2 style="margin:0 0 4px">${r.title}</h2>
    <p class="hint" style="margin:0 0 10px">${r.description || ""}</p>
    <div class="res-card-meta">
      <span class="res-stars">${starString(r.average_rating)}</span>
      <span>${r.rating_count} rating(s)</span>
      <span>${r.format}</span>
      <span>by ${r.uploaded_by}</span>
      <span>${fmtDate(r.uploaded_at)}</span>
    </div>
    <div class="res-card-meta" style="margin-top:6px">${axisAveragesHtml}</div>
    <div style="margin-top:10px">${openLink}</div>

    <div class="res-detail-section">
      <b>Rate this resource</b>
      <p class="hint" style="margin:4px 0 8px">Overall, plus how it did on each axis — this is what shows up in Compare.</p>
      <div class="res-rating-row" id="res-star-picker">${starPicker}</div>
      <div class="res-axis-rows" id="res-axis-rows">${axisPickersHtml}</div>
      <input id="res-comment" type="text" placeholder="optional comment" style="margin-top:6px;width:100%;font-family:var(--font-body);font-size:13px;padding:7px 8px;border:1px solid var(--line-strong);border-radius:var(--radius);background:var(--bg);color:var(--ink)">
      <button id="res-submit-rating" class="btn btn-primary" type="button" style="margin-top:8px">Submit rating</button>
    </div>

    <div class="res-detail-section">
      <b>Classify this resource</b>
      <div class="res-classify-row">
        <select id="res-classify-level">${levelOptions}</select>
      </div>
      <div class="res-classify-row" id="res-tag-toggles">${tagToggles}</div>
      <button id="res-submit-classify" class="btn btn-primary" type="button" style="margin-top:8px">Submit classification</button>
      <div style="margin-top:10px">${tagCountsHtml}</div>
    </div>

    <div class="res-detail-section">
      <b>Reviews</b>
      ${reviews}
    </div>
  `;

  el("res-star-picker").querySelectorAll(".res-star-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedStars = parseInt(btn.getAttribute("data-star"), 10);
      el("res-star-picker").querySelectorAll(".res-star-btn").forEach((b) => {
        b.classList.toggle("is-filled", parseInt(b.getAttribute("data-star"), 10) <= state.selectedStars);
      });
    });
  });

  el("res-axis-rows").querySelectorAll(".res-axis-picker").forEach((picker) => {
    const axisKey = picker.closest(".res-axis-row").getAttribute("data-axis");
    picker.querySelectorAll(".res-star-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.selectedAxisStars[axisKey] = parseInt(btn.getAttribute("data-star"), 10);
        picker.querySelectorAll(".res-star-btn").forEach((b) => {
          b.classList.toggle("is-filled", parseInt(b.getAttribute("data-star"), 10) <= state.selectedAxisStars[axisKey]);
        });
      });
    });
  });

  el("res-submit-rating").addEventListener("click", async () => {
    if (!state.selectedStars) {
      setFooter("pick an overall star rating first");
      return;
    }
    const axisKeys = Object.keys(state.meta.axes || {});
    const missing = axisKeys.filter((k) => !state.selectedAxisStars[k]);
    if (missing.length) {
      setFooter(`rate ${missing.join(", ")} too — that's what makes Compare useful`);
      return;
    }
    await postJson(`/api/resources/${resourceId}/rate`, {
      student_id: studentId(), stars: state.selectedStars, comment: el("res-comment").value.trim(),
      axes: state.selectedAxisStars,
    });
    openDetail(resourceId);
    loadList();
  });

  el("res-tag-toggles").querySelectorAll(".res-tag-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tag = btn.getAttribute("data-tag");
      if (state.selectedTags.has(tag)) {
        state.selectedTags.delete(tag);
        btn.classList.remove("is-selected");
      } else {
        state.selectedTags.add(tag);
        btn.classList.add("is-selected");
      }
    });
  });

  el("res-submit-classify").addEventListener("click", async () => {
    await postJson(`/api/resources/${resourceId}/classify`, {
      student_id: studentId(), level: el("res-classify-level").value, tags: Array.from(state.selectedTags),
    });
    openDetail(resourceId);
    loadList();
  });

  el("res-compare-overlay").hidden = true;
  el("res-detail-overlay").hidden = false;
}

async function postJson(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
  } catch (e) {
    setFooter(e.message);
  }
}

el("res-detail-close").addEventListener("click", () => {
  el("res-detail-overlay").hidden = true;
});
el("res-detail-overlay").addEventListener("click", (e) => {
  if (e.target === el("res-detail-overlay")) el("res-detail-overlay").hidden = true;
});

bootstrap();
