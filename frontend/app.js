const state = {
  subjectId: null,
  subjects: [],
  topics: [],
  mastery: {},       // topic_id -> percent
  selectedTopic: null,
  mode: "chat",
  quiz: null,         // { attemptId, questions }
  code: null,         // { attemptId, task, rubric }
};

const el = (id) => document.getElementById(id);
const studentId = () => el("student-id").value.trim() || "student1";
const level = () => el("level-select").value;

function setStatus(text) { el("status-text").textContent = text; }

function sourcesHint(sources) {
  if (!sources || !sources.length) return "";
  return `<p class="hint sources-hint">grounded in: ${sources.join(", ")}</p>`;
}

function updateTopicIndicator() {
  const label = el("topic-indicator");
  if (state.selectedTopic) {
    const t = state.topics.find((t) => t.id === state.selectedTopic);
    label.textContent = `topic: ${t ? t.title : state.selectedTopic}`;
  } else {
    label.textContent = "no topic selected — will infer from your question";
  }
}

// ---------------------------------------------------------------------
// Topology strip -- the mastery map that doubles as topic navigation.
// Nodes are spaced evenly (works for any topic count, including ones
// added later via PDF ingestion) and colored along a grey -> green scale
// by mastery percent.
// ---------------------------------------------------------------------
function masteryColor(pct) {
  const from = [156, 166, 148];   // --line-strong
  const to = [61, 122, 92];       // --accent-grow
  const t = Math.max(0, Math.min(100, pct)) / 100;
  const rgb = from.map((f, i) => Math.round(f + (to[i] - f) * t));
  return `rgb(${rgb.join(",")})`;
}

function renderTopology() {
  const svg = el("topology-svg");
  const n = state.topics.length;
  if (n === 0) { svg.innerHTML = ""; return; }

  const width = 1000, laneY = 55, margin = 70;
  const step = n > 1 ? (width - margin * 2) / (n - 1) : 0;
  const xs = state.topics.map((_, i) => margin + step * i);

  let svgParts = [];

  for (let i = 0; i < n - 1; i++) {
    svgParts.push(
      `<line class="topo-edge" x1="${xs[i]}" y1="${laneY}" x2="${xs[i + 1]}" y2="${laneY}"/>`
    );
  }

  state.topics.forEach((t, i) => {
    const pct = state.mastery[t.id] ?? 0;
    const r = 14;
    const fillT = pct / 100;
    const selected = state.selectedTopic === t.id ? "is-selected" : "";
    svgParts.push(`
      <g data-topic="${t.id}" class="topo-node-group" style="cursor:pointer">
        <circle class="topo-node ${selected}" cx="${xs[i]}" cy="${laneY}" r="${r}"
          fill="${masteryColor(pct)}" fill-opacity="${0.25 + 0.6 * fillT}"/>
        <text class="topo-pct" x="${xs[i]}" y="${laneY + 4}">${pct}</text>
        <text class="topo-label" x="${xs[i]}" y="${laneY + 32}">${t.title.split(" ")[0]}</text>
      </g>
    `);
  });

  svg.innerHTML = svgParts.join("");

  svg.querySelectorAll(".topo-node-group").forEach((g) => {
    g.addEventListener("click", () => {
      state.selectedTopic = g.getAttribute("data-topic");
      updateTopicIndicator();
      renderTopology();
    });
  });
}

async function loadTopicsAndMastery() {
  const topics = await fetch(`/api/topics?subject_id=${encodeURIComponent(state.subjectId)}`).then((r) => r.json());
  state.topics = topics;
  state.selectedTopic = null;
  await refreshMastery();
  renderTopology();
  updateTopicIndicator();
}

async function refreshMastery() {
  if (!state.subjectId) return null;
  try {
    const data = await fetch(
      `/api/progress?student_id=${encodeURIComponent(studentId())}&level=${level()}&subject_id=${encodeURIComponent(state.subjectId)}`
    ).then((r) => r.json());
    state.mastery = {};
    data.mastery.forEach((m) => { state.mastery[m.topic_id] = m.percent; });
    return data;
  } catch (e) {
    return null;
  }
}

// ---------------------------------------------------------------------
// Subject picker
// ---------------------------------------------------------------------
function renderSubjectList() {
  const list = el("subject-list");
  list.innerHTML = state.subjects
    .map(
      (s) => `
      <button class="subject-card" data-subject="${s.id}" type="button">
        <div class="subject-card-title">${s.title}</div>
        <div class="subject-card-meta">${s.source === "uploaded" ? "from your pdf" : "offered by us"} · ${s.topic_count} topics${s.rag ? ' · <span class="rag-badge">retrieval-grounded</span>' : ""}</div>
        ${s.description ? `<div class="subject-card-desc">${s.description}</div>` : ""}
      </button>`
    )
    .join("");

  list.querySelectorAll(".subject-card").forEach((card) => {
    card.addEventListener("click", () => selectSubject(card.getAttribute("data-subject")));
  });
}

async function loadSubjects() {
  const subjects = await fetch("/api/subjects").then((r) => r.json());
  state.subjects = subjects;
  renderSubjectList();
}

function showSubjectPicker() {
  el("subject-picker").hidden = false;
  el("app-shell").hidden = true;
}

async function selectSubject(subjectId) {
  state.subjectId = subjectId;
  const subject = state.subjects.find((s) => s.id === subjectId);
  el("brand-title").textContent = subject ? subject.title : "Tutor";
  el("subject-switch").textContent = subject ? subject.title : "choose a subject";
  el("subject-picker").hidden = true;
  el("app-shell").hidden = false;
  setStatus("loading topics…");
  await loadTopicsAndMastery();
  setStatus("ready");
  if (state.mode === "progress") loadProgressView();
}

el("subject-switch").addEventListener("click", showSubjectPicker);

el("subject-pdf-submit").addEventListener("click", async () => {
  const fileInput = el("subject-pdf-input");
  const file = fileInput.files[0];
  const statusEl = el("subject-upload-status");
  if (!file) {
    statusEl.textContent = "Pick a PDF file first.";
    statusEl.style.color = "var(--accent-warn)";
    return;
  }
  const form = new FormData();
  form.append("file", file);
  const title = el("subject-pdf-title").value.trim();
  if (title) form.append("title", title);

  statusEl.style.color = "var(--muted)";
  statusEl.textContent = "Reading your PDF and building topics…";
  el("subject-pdf-submit").disabled = true;
  try {
    const res = await fetch("/api/subjects/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed");

    statusEl.style.color = "var(--accent-grow)";
    const ragNote = data.subject.rag ? " Answers on this subject will retrieve relevant excerpts (RAG)." : "";
    statusEl.textContent = `Built "${data.subject.title}" with ${data.subject.topic_count} topics.${ragNote}`;
    if (data.note) {
      statusEl.textContent += ` (${data.note})`;
    }
    await loadSubjects();
    await selectSubject(data.subject.id);
  } catch (e) {
    statusEl.style.color = "var(--accent-warn)";
    statusEl.textContent = e.message;
  } finally {
    el("subject-pdf-submit").disabled = false;
  }
});

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.remove("is-active");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("is-active");
    tab.setAttribute("aria-selected", "true");

    const mode = tab.dataset.mode;
    state.mode = mode;
    document.querySelectorAll(".mode-view").forEach((v) => (v.hidden = true));
    el(`mode-${mode}`).hidden = false;

    if (mode === "progress" && state.subjectId) loadProgressView();
  });
});

// ---------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------
el("chat-send").addEventListener("click", async () => {
  const question = el("chat-input").value.trim();
  if (!question) return;
  setStatus("thinking…");
  el("chat-send").disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student_id: studentId(),
        level: level(),
        subject_id: state.subjectId,
        topic_id: state.selectedTopic,
        question,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    state.selectedTopic = data.topic_id;
    updateTopicIndicator();
    renderTopology();

    el("chat-output").innerHTML = `
      <p class="hint">topic: ${data.topic_title}</p>
      <div class="code-feedback">${data.reply}</div>
      ${sourcesHint(data.sources)}
    `;
  } catch (e) {
    el("chat-output").innerHTML = `<p style="color:var(--accent-warn)">${e.message}</p>`;
  } finally {
    setStatus("ready");
    el("chat-send").disabled = false;
  }
});

// ---------------------------------------------------------------------
// Quiz
// ---------------------------------------------------------------------
el("quiz-start").addEventListener("click", async () => {
  if (!state.selectedTopic) {
    el("quiz-output").innerHTML = `<p style="color:var(--accent-warn)">Pick a topic node above first.</p>`;
    return;
  }
  setStatus("generating quiz…");
  el("quiz-start").disabled = true;
  try {
    const res = await fetch("/api/quiz/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId(), level: level(), subject_id: state.subjectId, topic_id: state.selectedTopic }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    state.quiz = { attemptId: data.attempt_id, questions: data.questions };
    renderQuizForm(data.topic_title, data.questions, data.sources);
  } catch (e) {
    el("quiz-output").innerHTML = `<p style="color:var(--accent-warn)">${e.message}</p>`;
  } finally {
    setStatus("ready");
    el("quiz-start").disabled = false;
  }
});

function renderQuizForm(topicTitle, questions, sources) {
  const html = questions
    .map(
      (q, qi) => `
    <div class="quiz-q">
      <p class="quiz-q-title">${qi + 1}. ${q.question}</p>
      ${q.options
        .map(
          (opt, oi) => `
        <label class="quiz-opt">
          <input type="radio" name="q${qi}" value="${oi}"> ${opt}
        </label>`
        )
        .join("")}
    </div>`
    )
    .join("");

  el("quiz-output").innerHTML = `
    <p class="hint">topic: ${topicTitle}</p>
    ${sourcesHint(sources)}
    ${html}
    <button id="quiz-submit" class="btn btn-primary">Submit answers</button>
  `;

  el("quiz-submit").addEventListener("click", submitQuiz);
}

async function submitQuiz() {
  const answers = state.quiz.questions.map((_, qi) => {
    const picked = document.querySelector(`input[name="q${qi}"]:checked`);
    return picked ? parseInt(picked.value, 10) : -1;
  });

  setStatus("grading…");
  try {
    const res = await fetch("/api/quiz/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id: state.quiz.attemptId, answers }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    const ev = data.evaluation;
    const results = ev.details.results;
    const html = results
      .map((r, i) => {
        const cls = r.correct ? "is-correct" : "is-wrong";
        return `
        <div class="quiz-q">
          <p class="quiz-q-title">${i + 1}. ${r.question}</p>
          <p class="quiz-opt ${cls}">${r.correct ? "Correct" : "Not quite"}</p>
          <p class="quiz-explain">${r.explanation}</p>
        </div>`;
      })
      .join("");

    el("quiz-output").innerHTML = `
      <div class="code-feedback">
        <span class="score-badge ${ev.score >= 70 ? "is-strong" : "is-weak"}">${ev.score}%</span>
        <p>${ev.feedback}</p>
      </div>
      ${html}
      <div class="planner-note">Next up: ${data.next_topic_suggestion}. ${data.planner_reason}</div>
    `;
    renderTopology();
  } catch (e) {
    el("quiz-output").innerHTML = `<p style="color:var(--accent-warn)">${e.message}</p>`;
  } finally {
    setStatus("ready");
  }
}

// ---------------------------------------------------------------------
// Code task
// ---------------------------------------------------------------------
el("code-start").addEventListener("click", async () => {
  if (!state.selectedTopic) {
    el("code-output").innerHTML = `<p style="color:var(--accent-warn)">Pick a topic node above first.</p>`;
    return;
  }
  setStatus("generating task…");
  el("code-start").disabled = true;
  try {
    const res = await fetch("/api/code/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId(), level: level(), subject_id: state.subjectId, topic_id: state.selectedTopic }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    state.code = { attemptId: data.attempt_id };
    el("code-output").innerHTML = `
      <p class="hint">topic: ${data.topic_title}</p>
      ${sourcesHint(data.sources)}
      <p>${data.task}</p>
      <ul class="rubric">${data.rubric.map((r) => `<li>${r}</li>`).join("")}</ul>
      <textarea id="code-submission" rows="8" placeholder="Paste your solution here…"></textarea>
      <button id="code-submit" class="btn btn-primary">Submit for grading</button>
    `;
    el("code-submit").addEventListener("click", submitCode);
  } catch (e) {
    el("code-output").innerHTML = `<p style="color:var(--accent-warn)">${e.message}</p>`;
  } finally {
    setStatus("ready");
    el("code-start").disabled = false;
  }
});

async function submitCode() {
  const submission = el("code-submission").value.trim();
  if (!submission) return;
  setStatus("grading…");
  try {
    const res = await fetch("/api/code/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id: state.code.attemptId, submission }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    const ev = data.evaluation;
    el("code-output").innerHTML += `
      <div class="code-feedback">
        <span class="score-badge ${ev.score >= 70 ? "is-strong" : "is-weak"}">${ev.score}/100</span>
        <p>${ev.feedback}</p>
        <p><strong>Strengths</strong></p>
        <ul class="rubric">${ev.details.strengths.map((s) => `<li>${s}</li>`).join("")}</ul>
        <p><strong>To improve</strong></p>
        <ul class="rubric">${ev.details.improvements.map((s) => `<li>${s}</li>`).join("")}</ul>
      </div>
      <div class="planner-note">Next up: ${data.next_topic_suggestion}. ${data.planner_reason}</div>
    `;
    renderTopology();
  } catch (e) {
    el("code-output").innerHTML += `<p style="color:var(--accent-warn)">${e.message}</p>`;
  } finally {
    setStatus("ready");
  }
}

// ---------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------
async function loadProgressView() {
  setStatus("loading progress…");
  const data = await refreshMastery();
  renderTopology();
  if (!data) {
    el("progress-output").innerHTML = `<p style="color:var(--accent-warn)">Could not load progress.</p>`;
    setStatus("ready");
    return;
  }
  const rows = data.mastery
    .map(
      (m) => `
    <div class="mastery-row">
      <div class="mastery-title">${m.title}</div>
      <div class="mastery-bar-track"><div class="mastery-bar-fill" style="width:${m.percent}%"></div></div>
      <div class="mastery-pct">${m.percent}%</div>
    </div>`
    )
    .join("");
  el("progress-output").innerHTML = `
    ${rows}
    <div class="planner-note">Suggested next: <strong>${data.next_topic_suggestion}</strong>. ${data.planner_reason}</div>
  `;
  setStatus("ready");
}

// ---------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------
loadSubjects();
showSubjectPicker();
