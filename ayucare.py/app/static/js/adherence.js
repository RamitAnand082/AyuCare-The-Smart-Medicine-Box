// adherence.js
// Standalone logic for the Adherence Score page.

let adherenceChart = null;

document.addEventListener("DOMContentLoaded", () => {
  const refreshBtn = document.getElementById("refresh-adherence-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      loadAdherence();
    });
  }

  setupNotificationUI();
  initNotificationsSocket();
  loadNotifications();

  loadAdherence();
});

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

