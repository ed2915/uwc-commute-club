const days = [
  ["mon", "Mon"],
  ["tue", "Tue"],
  ["wed", "Wed"],
  ["thu", "Thu"],
  ["fri", "Fri"]
];

const timeSlots = [
  "06:30",
  "06:45",
  "07:00",
  "07:15",
  "07:30",
  "07:45",
  "08:00",
  "08:15",
  "08:30",
  "09:00",
  "10:30",
  "12:30",
  "14:30",
  "15:30",
  "16:30",
  "17:30",
  "18:00",
  "18:30",
  "19:00",
  "19:30"
];

const form = document.querySelector("#tripForm");
const statusMessage = document.querySelector("#status");
const scheduleGrid = document.querySelector("#scheduleGrid");
const suburbLabel = document.querySelector("#suburbLabel");
const toUwcRoutes = document.querySelector("#toUwcRoutes");
const fromUwcRoutes = document.querySelector("#fromUwcRoutes");
const uniqueUserCount = document.querySelector("#uniqueUserCount");
const studentNumberInput = form.elements.studentNumber;
const removeStudentNumberButton = document.querySelector("#removeStudentNumber");
const selectedHeatmapDays = {
  to_uwc: "mon",
  from_uwc: "mon"
};

renderScheduleGrid();
loadPopularRoutes();
updateSuburbLabel();

form.addEventListener("change", (event) => {
  if (event.target.name === "direction") updateSuburbLabel();
});

studentNumberInput.addEventListener("input", () => {
  studentNumberInput.value = normalizeStudentNumber(studentNumberInput.value);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("", "");

  const formData = new FormData(form);
  const payload = {
    direction: formData.get("direction"),
    area: clean(formData.get("area")),
    schedule: formData.getAll("schedule"),
    studentNumber: normalizeStudentNumber(formData.get("studentNumber")),
    privacyConsent: formData.get("privacyConsent") === "yes"
  };

  if (!isValidStudentNumber(payload.studentNumber)) {
    setStatus("Use a valid 7-digit student number.", "error");
    return;
  }

  if (payload.schedule.length === 0) {
    setStatus("Please choose at least one day and time in the schedule.", "error");
    return;
  }

  if (!payload.privacyConsent) {
    setStatus("Please consent to the privacy terms before joining route pools.", "error");
    return;
  }

  const button = form.querySelector("button");
  button.disabled = true;

  try {
    const response = await fetch("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Could not save your trip");
    }

    form.reset();
    if (result.added === 0) {
      setStatus("Those route and time choices were already linked to this student number.", "success");
    } else if (result.skippedDuplicates > 0) {
      setStatus("New route and time choices were added. Repeated choices were skipped.", "success");
    } else {
      setStatus("Your route and time choices were added.", "success");
    }
    loadPopularRoutes();
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

removeStudentNumberButton.addEventListener("click", async () => {
  setStatus("", "");

  const studentNumber = normalizeStudentNumber(studentNumberInput.value);
  if (!isValidStudentNumber(studentNumber)) {
    setStatus("Enter your 7-digit student number before removing it.", "error");
    return;
  }

  removeStudentNumberButton.disabled = true;

  try {
    const response = await fetch("/api/remove-student-number", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ studentNumber })
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Could not remove your student number");
    }

    if (result.deleted > 0) {
      form.reset();
      setStatus(`Removed that student number from the database and deleted ${result.deleted} linked route-pool entries.`, "success");
      loadPopularRoutes();
    } else {
      setStatus("No route-pool entries were found for that student number.", "success");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    removeStudentNumberButton.disabled = false;
  }
});

function renderScheduleGrid() {
  const headerCells = days.map(([, label]) => `<div class="schedule-head">${label}</div>`).join("");
  const rows = timeSlots.map((time) => {
    const cells = days.map(([day, label], index) => {
      const value = `${day}@${time}`;
      const edgeClass = index === days.length - 1 ? " schedule-cell-edge" : "";
      return `
        <label class="schedule-cell${edgeClass}">
          <input type="checkbox" name="schedule" value="${value}">
          <span aria-label="${label} at ${time}"></span>
        </label>
      `;
    }).join("");

    return `
      <div class="schedule-time">${time}</div>
      ${cells}
    `;
  }).join("");

  scheduleGrid.innerHTML = `
    <div class="schedule-corner">Time</div>
    ${headerCells}
    ${rows}
  `;
}

async function loadPopularRoutes() {
  try {
    const response = await fetch("/api/popular-routes");
    const result = await response.json();
    uniqueUserCount.textContent = String(result.uniqueUsers || 0);
    renderPopularRoutes(result.routes || []);
  } catch {
    uniqueUserCount.textContent = "0";
    toUwcRoutes.innerHTML = `<p class="empty-routes">Popular routes could not be loaded.</p>`;
    fromUwcRoutes.innerHTML = `<p class="empty-routes">Popular routes could not be loaded.</p>`;
  }
}

function renderPopularRoutes(routes) {
  const toRoutes = routes.filter((route) => route.direction === "to_uwc");
  const fromRoutes = routes.filter((route) => route.direction === "from_uwc");

  renderRouteHeatmap(toUwcRoutes, toRoutes, "to_uwc");
  renderRouteHeatmap(fromUwcRoutes, fromRoutes, "from_uwc");
}

function renderRouteHeatmap(container, routes, direction) {
  if (routes.length === 0) {
    container.innerHTML = `<p class="empty-routes">No routes yet.</p>`;
    return;
  }

  const selectedDay = selectedHeatmapDays[direction];
  const visibleRoutes = routes.filter((route) => getScheduleDay(route.schedule) === selectedDay);
  const suburbs = [...new Set(visibleRoutes.map((route) => route.area))].sort((a, b) => a.localeCompare(b));
  const schedules = timeSlots.map((time) => `${selectedDay}@${time}`);
  const counts = new Map(visibleRoutes.map((route) => [`${route.area}|${route.schedule}`, route.interested]));
  const maxInterest = Math.max(...routes.map((route) => route.interested), 1);
  const headerCells = schedules.map((schedule) => `<div class="heatmap-head"><span>${escapeHtml(formatScheduleTime(schedule))}</span></div>`).join("");
  const rows = suburbs.map((suburb) => {
    const cells = schedules.map((schedule) => {
      const count = counts.get(`${suburb}|${schedule}`) || 0;
      const level = count === 0 ? 0 : Math.max(1, Math.ceil((count / maxInterest) * 5));

      return `<div class="heatmap-cell heatmap-level-${level}" aria-label="${escapeHtml(suburb)}, ${escapeHtml(formatSchedule(schedule))}: ${count} interested">${count || ""}</div>`;
    }).join("");

    return `
      <div class="heatmap-suburb">${escapeHtml(suburb)}</div>
      ${cells}
    `;
  }).join("");
  const grid = visibleRoutes.length === 0 ? `<p class="empty-routes">No routes for ${escapeHtml(formatDay(selectedDay))} yet.</p>` : `
    <div class="heatmap-wrap">
      <div class="heatmap-grid" style="grid-template-columns: minmax(116px, 1fr) repeat(${schedules.length}, 24px);">
        <div class="heatmap-corner">Suburb</div>
        ${headerCells}
        ${rows}
      </div>
    </div>
  `;

  container.innerHTML = `
    ${grid}
    <div class="day-tabs" role="tablist" aria-label="${direction === "to_uwc" ? "To UWC" : "From UWC"} heatmap day">
      ${days.map(([day, label]) => `
        <button class="day-tab" type="button" data-direction="${direction}" data-day="${day}" aria-selected="${day === selectedDay}">
          ${label}
        </button>
      `).join("")}
    </div>
  `;

  container.querySelectorAll(".day-tab").forEach((button) => {
    button.addEventListener("click", () => {
      selectedHeatmapDays[button.dataset.direction] = button.dataset.day;
      renderRouteHeatmap(container, routes, direction);
    });
  });
}

function updateSuburbLabel() {
  const direction = new FormData(form).get("direction");
  suburbLabel.textContent = direction === "from_uwc" ? "Destination suburb" : "Starting suburb";
}

function formatSchedule(schedule) {
  const [day, time] = String(schedule || "").split("@");
  const dayLabel = days.find(([value]) => value === day)?.[1] || day;
  return `${dayLabel} ${time || ""}`.trim();
}

function formatScheduleTime(schedule) {
  return String(schedule || "").split("@")[1] || "";
}

function getScheduleDay(schedule) {
  return String(schedule || "").split("@")[0] || "mon";
}

function formatDay(day) {
  return days.find(([value]) => value === day)?.[1] || day;
}

function compareSchedules(first, second) {
  const [firstDay, firstTime] = String(first).split("@");
  const [secondDay, secondTime] = String(second).split("@");
  const firstDayIndex = days.findIndex(([value]) => value === firstDay);
  const secondDayIndex = days.findIndex(([value]) => value === secondDay);

  if (firstDayIndex !== secondDayIndex) return firstDayIndex - secondDayIndex;
  return String(firstTime).localeCompare(String(secondTime));
}

function clean(value) {
  return String(value || "").trim();
}

function normalizeStudentNumber(value) {
  return String(value || "")
    .replace(/\D/g, "")
    .slice(0, 7);
}

function isValidStudentNumber(value) {
  return /^\d{7}$/.test(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(message, tone) {
  statusMessage.textContent = message;
  statusMessage.dataset.tone = tone;
}
