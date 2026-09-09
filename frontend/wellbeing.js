const el = (id) => document.getElementById(id);
const studentId = () => el("student-id").value.trim() || "student1";

const state = {
  meta: { mood_scale: {}, exercises: [], crisis: {} },
  selectedMood: null,
};

function setFooter(text) {
  el("wb-footer-text").textContent = text;
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString();
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ---------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------
async function bootstrap() {
  const meta = await fetch("/api/wellbeing/meta").then((r) => r.json());
  state.meta = meta;

  // Crisis contact banner is always visible, not tucked behind a tab --
  // this should be findable without having to look for it.
  el("wb-crisis-text").textContent = meta.crisis.note;

  renderMoodPicker();
  renderExercises();
  await loadCheckins();
  await loadJournalHistory();
  await loadChatHistory();

  el("wb-mode-checkin").addEventListener("click", () => switchMode("checkin"));
  el("wb-mode-journal").addEventListener("click", () => switchMode("journal"));
  el("wb-mode-chat").addEventListener("click", () => switchMode("chat"));
  el("wb-mode-breathe").addEventListener("click", () => switchMode("breathe"));
  el("wb-mode-exercises").addEventListener("click", () => switchMode("exercises"));
  el("wb-mode-staff").addEventListener("click", () => switchMode("staff"));

  el("wb-checkin-submit").addEventListener("click", submitCheckin);
  el("wb-journal-submit").addEventListener("click", submitJournal);

  el("wb-chat-send").addEventListener("click", sendChat);
  el("wb-chat-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });

  el("wb-breathe-toggle").addEventListener("click", toggleBreathing);
  el("wb-breathe-pattern").addEventListener("change", stopBreathing);

  el("student-id").addEventListener("change", async () => {
    await loadCheckins();
    await loadJournalHistory();
    await loadChatHistory();
  });

  setFooter("ready");
}

function switchMode(mode) {
  const views = {
    checkin: "wb-checkin-view", journal: "wb-journal-view", chat: "wb-chat-view",
    breathe: "wb-breathe-view", exercises: "wb-exercises-view", staff: "wb-staff-view",
  };
  const tabs = {
    checkin: "wb-mode-checkin", journal: "wb-mode-journal", chat: "wb-mode-chat",
    breathe: "wb-mode-breathe", exercises: "wb-mode-exercises", staff: "wb-mode-staff",
  };
  for (const key of Object.keys(views)) {
    el(views[key]).hidden = key !== mode;
    el(tabs[key]).classList.toggle("is-active", key === mode);
  }
  if (mode === "staff") loadStaffQueue();
  if (mode !== "breathe") stopBreathing();
}

// ---------------------------------------------------------------------
// Check-in
// ---------------------------------------------------------------------
function renderMoodPicker() {
  const entries = Object.entries(state.meta.mood_scale); // [["1","struggling"], ...]
  el("wb-mood-picker").innerHTML = entries
    .map(([val, label]) => `<button type="button" class="wb-mood-btn" data-mood="${val}">${label}</button>`)
    .join("");
  el("wb-mood-picker").querySelectorAll(".wb-mood-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedMood = Number(btn.dataset.mood);
      el("wb-mood-picker").querySelectorAll(".wb-mood-btn").forEach((b) => b.classList.remove("is-selected"));
      btn.classList.add("is-selected");
    });
  });
}

async function submitCheckin() {
  if (!state.selectedMood) {
    el("wb-checkin-status").textContent = "Pick how you're feeling first.";
    el("wb-checkin-status").className = "viz-status is-error";
    return;
  }
  setFooter("saving check-in…");
  const res = await fetch("/api/wellbeing/checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ student_id: studentId(), mood: state.selectedMood, note: el("wb-mood-note").value }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    el("wb-checkin-status").textContent = err.detail || "Could not save check-in.";
    el("wb-checkin-status").className = "viz-status is-error";
    setFooter("error");
    return;
  }
  el("wb-mood-note").value = "";
  el("wb-checkin-status").textContent = "Saved.";
  el("wb-checkin-status").className = "viz-status is-done";
  await loadCheckins();
  setFooter("ready");
}

async function loadCheckins() {
  const rows = await fetch(`/api/wellbeing/checkins?student_id=${encodeURIComponent(studentId())}`).then((r) => r.json());
  const sorted = [...rows].sort((a, b) => b.at - a.at).slice(0, 20);
  if (!sorted.length) {
    el("wb-checkin-history").innerHTML = `<div class="wb-staff-empty">No check-ins yet.</div>`;
    return;
  }
  el("wb-checkin-history").innerHTML = sorted
    .map((c) => `
      <div class="wb-checkin-row">
        <span class="wb-checkin-mood">${escapeHtml(state.meta.mood_scale[c.mood] || c.mood)}</span>
        <span class="wb-checkin-note">${escapeHtml(c.note)}</span>
        <span class="wb-checkin-time">${fmtTime(c.at)}</span>
      </div>`)
    .join("");
}

// ---------------------------------------------------------------------
// Journal
// ---------------------------------------------------------------------
async function submitJournal() {
  const text = el("wb-journal-text").value.trim();
  if (!text) return;
  setFooter("reflecting…");
  el("wb-journal-submit").disabled = true;
  el("wb-journal-status").textContent = "";
  el("wb-journal-reflection").hidden = true;

  try {
    const res = await fetch("/api/wellbeing/journal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId(), text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not save entry.");
    }
    const data = await res.json();
    el("wb-journal-reflection").hidden = false;
    el("wb-journal-reflection").classList.toggle("is-flagged", data.flagged);
    el("wb-journal-reflection").textContent = data.reflection;
    el("wb-journal-text").value = "";
    await loadJournalHistory();
    setFooter("ready");
  } catch (e) {
    el("wb-journal-status").textContent = e.message;
    el("wb-journal-status").className = "viz-status is-error";
    setFooter("error");
  } finally {
    el("wb-journal-submit").disabled = false;
  }
}

async function loadJournalHistory() {
  const rows = await fetch(`/api/wellbeing/journal?student_id=${encodeURIComponent(studentId())}`).then((r) => r.json());
  const sorted = [...rows].sort((a, b) => b.at - a.at);
  if (!sorted.length) {
    el("wb-journal-history").innerHTML = `<div class="wb-staff-empty">No entries yet.</div>`;
    return;
  }
  el("wb-journal-history").innerHTML = sorted
    .map((e) => `
      <div class="wb-journal-entry">
        <p class="wb-journal-entry-text">${escapeHtml(e.text)}${e.flagged ? '<span class="wb-flag-badge">flagged</span>' : ""}</p>
        <div class="wb-journal-entry-reflection">${escapeHtml(e.reflection)}</div>
        <div class="wb-journal-entry-time">${fmtTime(e.at)}</div>
      </div>`)
    .join("");
}

// ---------------------------------------------------------------------
// Chat companion
// ---------------------------------------------------------------------
async function loadChatHistory() {
  const rows = await fetch(`/api/wellbeing/chat?student_id=${encodeURIComponent(studentId())}`).then((r) => r.json());
  renderChatMessages(rows);
}

function renderChatMessages(rows) {
  const sorted = [...rows].sort((a, b) => a.at - b.at);
  const box = el("wb-chat-messages");
  if (!sorted.length) {
    box.innerHTML = `<div class="wb-staff-empty">No messages yet -- say hi.</div>`;
    return;
  }
  box.innerHTML = sorted
    .map((m) => `
      <div class="wb-chat-msg wb-chat-msg-${m.role}${m.flagged ? " is-flagged" : ""}">
        <div class="wb-chat-bubble">${escapeHtml(m.text)}</div>
      </div>`)
    .join("");
  box.scrollTop = box.scrollHeight;
}

async function sendChat() {
  const text = el("wb-chat-text").value.trim();
  if (!text) return;
  el("wb-chat-send").disabled = true;
  el("wb-chat-status").textContent = "";
  el("wb-chat-text").value = "";

  // Optimistic render of the student's own message so the chat doesn't
  // feel frozen while the model is thinking.
  const box = el("wb-chat-messages");
  const empty = box.querySelector(".wb-staff-empty");
  if (empty) empty.remove();
  box.insertAdjacentHTML("beforeend", `
    <div class="wb-chat-msg wb-chat-msg-user"><div class="wb-chat-bubble">${escapeHtml(text)}</div></div>`);
  box.scrollTop = box.scrollHeight;
  setFooter("thinking…");

  try {
    const res = await fetch("/api/wellbeing/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId(), text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not send message.");
    }
    await loadChatHistory();
    setFooter("ready");
  } catch (e) {
    el("wb-chat-status").textContent = e.message;
    el("wb-chat-status").className = "viz-status is-error";
    setFooter("error");
  } finally {
    el("wb-chat-send").disabled = false;
  }
}

// ---------------------------------------------------------------------
// Breathing activity
// ---------------------------------------------------------------------
const BREATH_PATTERNS = {
  box: { phases: [
    { name: "Breathe in", seconds: 4 },
    { name: "Hold", seconds: 4 },
    { name: "Breathe out", seconds: 4 },
    { name: "Hold", seconds: 4 },
  ] },
  478: { phases: [
    { name: "Breathe in", seconds: 4 },
    { name: "Hold", seconds: 7 },
    { name: "Breathe out", seconds: 8 },
  ] },
  calm: { phases: [
    { name: "Breathe in", seconds: 4 },
    { name: "Breathe out", seconds: 6 },
  ] },
};

const breathing = { running: false, timer: null, phaseIdx: 0, cycles: 0 };

function stopBreathing() {
  breathing.running = false;
  if (breathing.timer) clearTimeout(breathing.timer);
  breathing.timer = null;
  const circle = el("wb-breathe-circle");
  circle.classList.remove("is-expand", "is-contract");
  circle.style.transitionDuration = "";
  el("wb-breathe-phase").textContent = "Ready";
  el("wb-breathe-toggle").textContent = "Start";
  el("wb-breathe-count").textContent = "";
}

function runBreathPhase() {
  const pattern = BREATH_PATTERNS[el("wb-breathe-pattern").value];
  const phase = pattern.phases[breathing.phaseIdx];
  const circle = el("wb-breathe-circle");
  circle.style.transitionDuration = `${phase.seconds}s`;
  circle.classList.toggle("is-expand", phase.name === "Breathe in");
  circle.classList.toggle("is-contract", phase.name === "Breathe out");
  el("wb-breathe-phase").textContent = `${phase.name}… ${phase.seconds}s`;

  breathing.timer = setTimeout(() => {
    if (!breathing.running) return;
    breathing.phaseIdx = (breathing.phaseIdx + 1) % pattern.phases.length;
    if (breathing.phaseIdx === 0) {
      breathing.cycles += 1;
      el("wb-breathe-count").textContent = `${breathing.cycles} cycle${breathing.cycles === 1 ? "" : "s"}`;
    }
    runBreathPhase();
  }, phase.seconds * 1000);
}

function toggleBreathing() {
  if (breathing.running) {
    stopBreathing();
    return;
  }
  breathing.running = true;
  breathing.phaseIdx = 0;
  breathing.cycles = 0;
  el("wb-breathe-toggle").textContent = "Stop";
  runBreathPhase();
}

// ---------------------------------------------------------------------
// Exercises
// ---------------------------------------------------------------------
function renderExercises() {
  el("wb-exercises-list").innerHTML = state.meta.exercises
    .map((ex) => `
      <div class="wb-exercise-card">
        <p class="wb-exercise-title">${escapeHtml(ex.title)}</p>
        <p class="wb-exercise-meta">${escapeHtml(ex.category)} · ~${ex.duration_min} min</p>
        <ol class="wb-exercise-steps">
          ${ex.steps.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}
        </ol>
      </div>`)
    .join("");
}

// ---------------------------------------------------------------------
// Staff review queue
// ---------------------------------------------------------------------
async function loadStaffQueue() {
  el("wb-staff-list").innerHTML = `<div class="wb-staff-empty">Loading…</div>`;
  const rows = await fetch("/api/wellbeing/staff/flags").then((r) => r.json());
  if (!rows.length) {
    el("wb-staff-list").innerHTML = `<div class="wb-staff-empty">No flagged entries.</div>`;
    return;
  }
  el("wb-staff-list").innerHTML = rows
    .map((e) => `
      <div class="wb-staff-card" data-student="${escapeHtml(e.student_id)}" data-entry="${escapeHtml(e.id)}">
        <div class="wb-staff-card-head">
          <span class="wb-staff-student">${escapeHtml(e.student_id)}</span>
          <span class="wb-staff-source">${escapeHtml(e.source || "journal")}</span>
          <span class="wb-staff-time">${fmtTime(e.at)}</span>
        </div>
        <p class="wb-staff-text">${escapeHtml(e.text)}</p>
        <div class="wb-staff-controls">
          <select class="wb-staff-status">
            <option value="open" ${e.staff_status === "open" ? "selected" : ""}>open</option>
            <option value="reviewing" ${e.staff_status === "reviewing" ? "selected" : ""}>reviewing</option>
            <option value="resolved" ${e.staff_status === "resolved" ? "selected" : ""}>resolved</option>
          </select>
          <button type="button" class="btn wb-staff-save">Save</button>
        </div>
      </div>`)
    .join("");

  el("wb-staff-list").querySelectorAll(".wb-staff-save").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".wb-staff-card");
      const status = card.querySelector(".wb-staff-status").value;
      await fetch(`/api/wellbeing/staff/flags/${encodeURIComponent(card.dataset.student)}/${encodeURIComponent(card.dataset.entry)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ staff_status: status }),
      });
      await loadStaffQueue();
    });
  });
}

bootstrap();
