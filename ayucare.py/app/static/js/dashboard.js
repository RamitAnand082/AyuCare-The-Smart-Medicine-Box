// dashboard.js
// Handles patient CRUD, schedule updates, live dose status, and
// real-time notifications using Fetch API + Socket.IO.

let socket = null;

document.addEventListener("DOMContentLoaded", () => {
  const addPatientForm = document.getElementById("add-patient-form");
  const addMedicineEntryForm = document.getElementById("add-medicine-entry-form");
  const refreshMedicineEntriesBtn = document.getElementById("refresh-medicine-entries-btn");
  const navDashboardBtn = document.getElementById("nav-dashboard");
  const navAdherenceBtn = document.getElementById("nav-adherence");
  const adherenceRefreshBtn = document.getElementById("refresh-adherence-btn");

  if (addPatientForm) {
    addPatientForm.addEventListener("submit", onAddPatientSubmit);
  }

  if (addMedicineEntryForm) {
    addMedicineEntryForm.addEventListener("submit", onAddMedicineEntrySubmit);
  }

  if (refreshMedicineEntriesBtn) {
    refreshMedicineEntriesBtn.addEventListener("click", () => {
      loadMedicineEntries();
    });
  }

  if (navDashboardBtn && navAdherenceBtn) {
    navDashboardBtn.addEventListener("click", () => {
      switchToDashboardView();
    });
    navAdherenceBtn.addEventListener("click", () => {
      switchToAdherenceView();
    });
  }

  if (adherenceRefreshBtn) {
    adherenceRefreshBtn.addEventListener("click", () => {
      loadAdherence();
    });
  }

  setupNotificationUI();
  initNotificationsSocket();

  // Initial load
  loadPatients();
  // Defer adherence loading until user opens the tab for the first time
  loadNotifications();
  loadMedicineEntries();
});

async function onAddPatientSubmit(event) {
  event.preventDefault();
  const form = event.target;

  const payload = {
    name: form.name.value.trim(),
    age: form.age.value,
    box_id: form.box_id.value.trim(),
  };

  try {
    const res = await fetch("/dashboard/patients", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to add patient.", "error");
      return;
    }

    showToast("Patient added successfully.", "success");
    form.reset();
    loadPatients();
  } catch (err) {
    console.error(err);
    showToast("Network error while adding patient.", "error");
  }
}

async function loadPatients() {
  try {
    const res = await fetch("/dashboard/patients");
    const data = await res.json();
    const patients = data.patients || [];
    renderPatientsTable(patients);
    renderMedicineEntryPatientSelect(patients);
  } catch (err) {
    console.error(err);
    showToast("Failed to load patients.", "error");
  }
}

function renderMedicineEntryPatientSelect(patients) {
  const select = document.getElementById("med-entry-patient");
  if (!select) return;
  select.innerHTML = "";
  patients.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = String(p.id);
    opt.textContent = `${p.name} (${p.box_id})`;
    select.appendChild(opt);
  });
}

async function onAddMedicineEntrySubmit(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    patient_id: form.patient_id.value,
    medicine_name: form.medicine_name.value.trim(),
    slot: form.slot.value,
    dose_time: form.dose_time.value.trim(),
    note: form.note.value.trim(),
  };

  try {
    const res = await fetch("/dashboard/medicine_entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to add medicine entry.", "error");
      return;
    }
    showToast("Medicine entry added.", "success");
    form.reset();
    loadMedicineEntries();
  } catch (err) {
    console.error(err);
    showToast("Network error while adding medicine entry.", "error");
  }
}

async function loadMedicineEntries() {
  try {
    const res = await fetch("/dashboard/medicine_entries");
    const data = await res.json();
    const entries = data.entries || [];
    renderMedicineEntriesTable(entries);
  } catch (err) {
    console.error(err);
  }
}

function renderMedicineEntriesTable(entries) {
  const tbody = document.querySelector("#medicine-entries-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!entries.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "No medicine entries yet.";
    cell.style.fontSize = "0.85rem";
    cell.style.color = "#6b7280";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }

  entries.forEach((e) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(e.patient_name)}</td>
      <td>${escapeHtml(e.box_id)}</td>
      <td>${escapeHtml(e.medicine_name)}</td>
      <td>${escapeHtml(e.slot || "custom")}</td>
      <td>${escapeHtml(formatTo12Hour(e.dose_time))}</td>
      <td>${escapeHtml(e.note || "")}</td>
      <td>${e.active ? "Yes" : "No"}</td>
      <td></td>
    `;

    const actionsCell = row.children[7];
    const actionsWrapper = document.createElement("div");
    actionsWrapper.className = "action-buttons";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn btn-icon";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => {
      let newName = window.prompt("Medicine name:", e.medicine_name);
      if (newName === null) newName = e.medicine_name;
      let newSlot = window.prompt("Slot (morning/afternoon/night/custom):", e.slot || "custom");
      if (newSlot === null) newSlot = e.slot || "custom";
      let newTime = window.prompt("Dose time (e.g. 09:30 AM):", formatTo12Hour(e.dose_time));
      if (newTime === null) newTime = e.dose_time;
      let newNote = window.prompt("Note (optional):", e.note || "");
      if (newNote === null) newNote = e.note || "";

      updateMedicineEntry(e.id, {
        medicine_name: newName,
        slot: newSlot,
        dose_time: newTime,
        note: newNote,
      });
    });

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "btn btn-icon";
    toggleBtn.textContent = e.active ? "Disable" : "Enable";
    toggleBtn.addEventListener("click", () => {
      updateMedicineEntry(e.id, { active: !e.active });
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn btn-icon btn-danger";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => {
      if (confirm(`Delete medicine "${e.medicine_name}" for ${e.patient_name}?`)) {
        deleteMedicineEntry(e.id);
      }
    });

    actionsWrapper.appendChild(editBtn);
    actionsWrapper.appendChild(toggleBtn);
    actionsWrapper.appendChild(deleteBtn);
    actionsCell.appendChild(actionsWrapper);
    tbody.appendChild(row);
  });
}

async function updateMedicineEntry(entryId, payload) {
  try {
    const res = await fetch(`/dashboard/medicine_entries/${entryId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to update medicine entry.", "error");
      return;
    }
    showToast("Medicine entry updated.", "success");
    loadMedicineEntries();
  } catch (err) {
    console.error(err);
    showToast("Network error.", "error");
  }
}

async function deleteMedicineEntry(entryId) {
  try {
    const res = await fetch(`/dashboard/medicine_entries/${entryId}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to delete medicine entry.", "error");
      return;
    }
    showToast("Medicine entry deleted.", "success");
    loadMedicineEntries();
    loadStatus();
  } catch (err) {
    console.error(err);
    showToast("Network error while deleting medicine entry.", "error");
  }
}

function renderPatientsTable(patients) {
  const tbody = document.querySelector("#patients-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!patients.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "No patients yet. Add one using the form above.";
    cell.style.fontSize = "0.85rem";
    cell.style.color = "#6b7280";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }

  patients.forEach((p) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(p.name)}</td>
      <td>${p.age}</td>
      <td>${escapeHtml(p.box_id)}</td>
      <td></td>
    `;

    const actionsCell = row.children[3];
    const actionsWrapper = document.createElement("div");
    actionsWrapper.className = "action-buttons";

    const testNotifBtn = document.createElement("button");
    testNotifBtn.type = "button";
    testNotifBtn.className = "btn btn-icon";
    testNotifBtn.title = "Simulate patient pressing button (legacy test) – check notification bell";
    testNotifBtn.textContent = "Test notification";
    testNotifBtn.addEventListener("click", () => simulateDoseAndCheckNotification(p.box_id, p.name));

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn btn-icon btn-danger";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => {
      if (confirm(`Delete patient \"${p.name}\"? This cannot be undone.`)) {
        deletePatient(p.id);
      }
    });

    actionsWrapper.appendChild(testNotifBtn);
    actionsWrapper.appendChild(deleteBtn);
    actionsCell.appendChild(actionsWrapper);
    tbody.appendChild(row);
  });
}

async function updateSchedule(patientId, schedule, medicines = {}, times = {}) {
  try {
    const payload = {
      morning: !!schedule.morning,
      afternoon: !!schedule.afternoon,
      night: !!schedule.night,
      morning_medicine: medicines.morning ?? "",
      afternoon_medicine: medicines.afternoon ?? "",
      night_medicine: medicines.night ?? "",
    };
    if (times && (times.morning || times.afternoon || times.night)) {
      payload.morning_time = times.morning || "08:00";
      payload.afternoon_time = times.afternoon || "14:00";
      payload.night_time = times.night || "20:00";
    }
    const res = await fetch(`/dashboard/patients/${patientId}/schedule`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to update schedule.", "error");
      return;
    }

    showToast("Schedule updated.", "success");
    loadPatients();
    loadStatus();
  } catch (err) {
    console.error(err);
    showToast("Network error while updating schedule.", "error");
  }
}

async function simulateDoseAndCheckNotification(boxId, patientName) {
  try {
    const res = await fetch("/api/update_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        box_id: boxId,
        dose_time: "morning",
        status: "taken",
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Simulate failed.", "error");
      return;
    }
    showToast("Simulated dose for " + patientName + ". Check the notification bell above.", "success");
    loadStatus();
  } catch (err) {
    console.error(err);
    showToast("Network error.", "error");
  }
}

async function deletePatient(patientId) {
  try {
    const res = await fetch(`/dashboard/patients/${patientId}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to delete patient.", "error");
      return;
    }

    showToast("Patient deleted.", "success");
    loadPatients();
    loadStatus();
  } catch (err) {
    console.error(err);
    showToast("Network error while deleting patient.", "error");
  }
}

async function loadStatus() {
  try {
    const res = await fetch("/dashboard/status");
    const data = await res.json();
    const patients = data.patients || [];
    renderStatusTable(patients);
  } catch (err) {
    console.error(err);
    showToast("Failed to load dose status.", "error");
  }
}

function renderStatusTable(patients) {
  const tbody = document.querySelector("#status-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!patients.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.textContent = "No patients or schedules configured.";
    cell.style.fontSize = "0.85rem";
    cell.style.color = "#6b7280";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }

  patients.forEach((p) => {
    const row = document.createElement("tr");
    const status = p.status || {};
    const meds = p.medicines || {};
    const times = p.times || { morning: "08:00", afternoon: "14:00", night: "20:00" };
    const timesStr = `${formatTo12Hour(times.morning || "08:00")} / ${formatTo12Hour(times.afternoon || "14:00")} / ${formatTo12Hour(times.night || "20:00")}`;

    row.innerHTML = `
      <td>${escapeHtml(p.name)}</td>
      <td>${p.age}</td>
      <td>${escapeHtml(p.box_id)}</td>
      <td>${renderStatusCell(status.morning, meds.morning, formatTo12Hour(times.morning))}</td>
      <td>${renderStatusCell(status.afternoon, meds.afternoon, formatTo12Hour(times.afternoon))}</td>
      <td>${renderStatusCell(status.night, meds.night, formatTo12Hour(times.night))}</td>
      <td style="font-size:0.8rem;color:#6b7280;">${escapeHtml(timesStr)}</td>
    `;

    tbody.appendChild(row);
  });
}

function renderStatusCell(state, medicinesText, timeStr) {
  const badgeHtml = renderStatusBadge(state);
  const timeHtml = timeStr ? `<div style="font-size:0.7rem;color:#9ca3af;">${escapeHtml(timeStr)}</div>` : "";
  const medsHtml = medicinesText
    ? `<div style="margin-top:2px;font-size:0.75rem;color:#374151;">${formatMedicineList(medicinesText)}</div>`
    : "";
  return `
    <div>${badgeHtml}</div>
    ${timeHtml}
    ${medsHtml}
  `;
}

function renderStatusBadge(state) {
  if (!state) return "";
  const normalized = String(state).toLowerCase();
  let cls = "badge-pending";
  let label = "Pending";
  if (normalized === "taken") {
    cls = "badge-taken";
    label = "Taken";
  } else if (normalized === "missed") {
    cls = "badge-missed";
    label = "Missed";
  }
  return `<span class="badge ${cls}">${label}</span>`;
}

function formatMedicineList(raw) {
  // Allow comma-separated list, render each on its own line.
  const parts = String(raw)
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  if (!parts.length) return escapeHtml(String(raw));
  return parts
    .map((p) => `<div>${escapeHtml(p)}</div>`)
    .join("");
}

function formatTo12Hour(rawTime) {
  if (!rawTime) return "";
  const s = String(rawTime).trim().toUpperCase();

  // Already AM/PM
  if (s.endsWith("AM") || s.endsWith("PM")) return s;

  const parts = s.split(":");
  if (parts.length < 2) return String(rawTime);

  const hh = parseInt(parts[0], 10);
  const mm = parseInt(parts[1], 10);
  if (Number.isNaN(hh) || Number.isNaN(mm)) return String(rawTime);

  const suffix = hh >= 12 ? "PM" : "AM";
  const hour12 = (hh % 12) === 0 ? 12 : (hh % 12);
  return `${String(hour12).padStart(2, "0")}:${String(mm).padStart(2, "0")} ${suffix}`;
}

// ------------------ Notifications (Socket.IO + REST) -----------------------

function setupNotificationUI() {
  const toggleBtn = document.getElementById("notification-toggle");
  const dropdown = document.getElementById("notification-dropdown");
  const markAllBtn = document.getElementById("mark-all-read");

  if (!toggleBtn || !dropdown) return;

  toggleBtn.addEventListener("click", () => {
    const hidden = dropdown.hasAttribute("hidden");
    if (hidden) {
      dropdown.removeAttribute("hidden");
      loadNotifications();  // Refresh list when opening bell (catches any missed real-time updates)
    } else {
      dropdown.setAttribute("hidden", "true");
    }
  });

  document.addEventListener("click", (e) => {
    const bell = document.getElementById("notification-bell");
    if (!bell || !dropdown) return;
    if (!bell.contains(e.target)) {
      dropdown.setAttribute("hidden", "true");
    }
  });

  if (markAllBtn) {
    markAllBtn.addEventListener("click", () => markAllNotificationsRead());
  }
}

function initNotificationsSocket() {
  if (typeof io === "undefined") {
    console.warn("Socket.IO client not loaded.");
    return;
  }

  socket = io();

  socket.on("connect", () => {
    const doctorId = typeof window.AYUCARE_DOCTOR_ID !== "undefined" ? window.AYUCARE_DOCTOR_ID : null;
    socket.emit("join_dashboard", { doctor_id: doctorId });  // So server adds us to doctor_XX room
  });

  socket.on("dose_update", (payload) => {
    console.log("[Ayucare] dose_update received:", payload);
    addNotificationToUI(payload, true);
    incrementNotificationCount();
    loadStatus();
  });

  // Fallback: poll every 15 sec so hardware button notifications appear even if SocketIO misses
  setInterval(() => {
    loadNotifications();
    loadStatus();
  }, 15000);
}

async function loadNotifications() {
  try {
    const res = await fetch("/notifications/unread");
    const data = await res.json();
    const items = data.notifications || [];
    renderNotificationList(items);
  } catch (err) {
    console.error(err);
  }
}

function renderNotificationList(items) {
  const list = document.getElementById("notification-list");
  const countEl = document.getElementById("notification-count");
  if (!list || !countEl) return;

  list.innerHTML = "";

  if (!items.length) {
    const p = document.createElement("p");
    p.className = "notification-empty";
    p.textContent = "No notifications.";
    list.appendChild(p);
    countEl.textContent = "0";
    countEl.setAttribute("hidden", "true");
    return;
  }

  items.forEach((item) => {
    addNotificationToUI(item, false);
  });

  countEl.textContent = String(items.length);
  countEl.removeAttribute("hidden");
}

function addNotificationToUI(item, prepend) {
  const list = document.getElementById("notification-list");
  if (!list) return;

  const empty = list.querySelector(".notification-empty");
  if (empty) empty.remove();

  const el = document.createElement("div");
  el.className = "notification-item unread";
  el.dataset.id = item.id;

  const doseLabel = item.dose_time
    ? item.dose_time[0].toUpperCase() + item.dose_time.slice(1)
    : "";

  el.innerHTML = `
    <div class="notification-title">${escapeHtml(item.message)}</div>
    <div class="notification-meta">
      Box: ${escapeHtml(item.box_id || "")} · ${escapeHtml(
        doseLabel,
      )} · ${new Date(item.logged_at || Date.now()).toLocaleTimeString()}
    </div>
  `;

  el.addEventListener("click", () => markNotificationRead(item.id, el));

  if (prepend && list.firstChild) {
    list.insertBefore(el, list.firstChild);
  } else {
    list.appendChild(el);
  }
}

async function markNotificationRead(id, element) {
  try {
    const res = await fetch("/notifications/mark_read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to mark notification as read.", "error");
      return;
    }
    if (element) {
      element.classList.remove("unread");
      element.classList.add("read");
    }
    decrementNotificationCount();
  } catch (err) {
    console.error(err);
  }
}

async function markAllNotificationsRead() {
  try {
    const res = await fetch("/notifications/mark_read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || "Failed to mark all notifications as read.", "error");
      return;
    }
    const list = document.getElementById("notification-list");
    if (list) {
      list.querySelectorAll(".notification-item").forEach((el) => {
        el.classList.remove("unread");
        el.classList.add("read");
      });
    }
    resetNotificationCount();
  } catch (err) {
    console.error(err);
  }
}

function incrementNotificationCount() {
  const countEl = document.getElementById("notification-count");
  if (!countEl) return;
  const current = parseInt(countEl.textContent || "0", 10) || 0;
  const next = current + 1;
  countEl.textContent = String(next);
  if (next > 0) countEl.removeAttribute("hidden");
}

function decrementNotificationCount() {
  const countEl = document.getElementById("notification-count");
  if (!countEl) return;
  const current = parseInt(countEl.textContent || "0", 10) || 0;
  const next = Math.max(0, current - 1);
  countEl.textContent = String(next);
  if (next === 0) {
    countEl.setAttribute("hidden", "true");
  }
}

function resetNotificationCount() {
  const countEl = document.getElementById("notification-count");
  if (!countEl) return;
  countEl.textContent = "0";
  countEl.setAttribute("hidden", "true");
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message, type) {
  const container = document.getElementById("alert-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "toast-error" : "toast-success"}`;
  toast.innerHTML = `
    <span>${message}</span>
    <button type="button" aria-label="Dismiss notification">&times;</button>
  `;

  const button = toast.querySelector("button");
  button.addEventListener("click", () => {
    toast.remove();
  });

  container.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 4000);
}

// ------------------ Adherence Score View -----------------------

let adherenceChart = null;
let adherenceLoadedOnce = false;

function switchToDashboardView() {
  const mainSection = document.getElementById("main-dashboard-section");
  const adherenceSection = document.getElementById("adherence-section");
  const navDashboardBtn = document.getElementById("nav-dashboard");
  const navAdherenceBtn = document.getElementById("nav-adherence");

  if (mainSection) mainSection.hidden = false;
  if (adherenceSection) adherenceSection.hidden = true;

  if (navDashboardBtn) navDashboardBtn.classList.add("active");
  if (navAdherenceBtn) navAdherenceBtn.classList.remove("active");
}

function switchToAdherenceView() {
  const mainSection = document.getElementById("main-dashboard-section");
  const adherenceSection = document.getElementById("adherence-section");
  const navDashboardBtn = document.getElementById("nav-dashboard");
  const navAdherenceBtn = document.getElementById("nav-adherence");

  if (mainSection) mainSection.hidden = true;
  if (adherenceSection) adherenceSection.hidden = false;

  if (navDashboardBtn) navDashboardBtn.classList.remove("active");
  if (navAdherenceBtn) navAdherenceBtn.classList.add("active");

  if (!adherenceLoadedOnce) {
    loadAdherence();
    adherenceLoadedOnce = true;
  }
}

async function loadAdherence() {
  try {
    const res = await fetch("/all_adherence_data");
    const data = await res.json();
    const patients = data.patients || [];
    renderAdherenceCards(patients);
    renderAdherenceChart(patients);
  } catch (err) {
    console.error(err);
    showToast("Failed to load adherence data.", "error");
  }
}

function renderAdherenceCards(patients) {
  const container = document.getElementById("adherence-cards");
  if (!container) return;
  container.innerHTML = "";

  if (!patients.length) {
    const p = document.createElement("p");
    p.textContent = "No patients or dose logs yet.";
    p.style.fontSize = "0.85rem";
    p.style.color = "#6b7280";
    container.appendChild(p);
    return;
  }

  patients.forEach((p) => {
    const card = document.createElement("div");
    card.className = "adherence-card";

    const pct = typeof p.adherence_percent === "number" ? p.adherence_percent : 0;
    const taken = p.taken_count ?? 0;
    const missed = p.missed_count ?? 0;
    const total = p.total_events ?? taken + missed;

    let gradeClass = "adherence-grade--poor";
    if (pct >= 75) {
      gradeClass = "adherence-grade--good";
    } else if (pct >= 50) {
      gradeClass = "adherence-grade--medium";
    }

    card.innerHTML = `
      <div class="adherence-card-header">
        <span>${escapeHtml(p.name)} (${escapeHtml(p.box_id)})</span>
        <span class="adherence-grade ${gradeClass}">${escapeHtml(p.grade || "")}</span>
      </div>
      <div class="adherence-meta-row">
        <span>Adherence:</span>
        <span><strong>${pct.toFixed(1)}%</strong></span>
      </div>
      <div class="adherence-meta-row">
        <span>Taken / Missed:</span>
        <span>${taken} / ${missed}</span>
      </div>
      <div class="adherence-meta-row">
        <span>Total events:</span>
        <span>${total}</span>
      </div>
    `;

    container.appendChild(card);
  });
}

function renderAdherenceChart(patients) {
  const canvas = document.getElementById("adherence-chart");
  if (!canvas || typeof Chart === "undefined") return;

  const ctx = canvas.getContext("2d");
  const labels = patients.map((p) => p.name || p.box_id || "Patient");
  const data = patients.map((p) =>
    typeof p.adherence_percent === "number" ? p.adherence_percent : 0,
  );

  const bgColors = data.map((pct) => {
    if (pct >= 75) return "rgba(34, 197, 94, 0.8)"; // green
    if (pct >= 50) return "rgba(245, 158, 11, 0.85)"; // orange
    return "rgba(239, 68, 68, 0.85)"; // red
  });

  if (adherenceChart) {
    adherenceChart.destroy();
  }

  adherenceChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Adherence %",
          data,
          backgroundColor: bgColors,
          borderRadius: 6,
          borderSkipped: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: {
            callback: (value) => `${value}%`,
          },
        },
      },
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.parsed.y.toFixed(1)}%`,
          },
        },
      },
    },
  });
}

