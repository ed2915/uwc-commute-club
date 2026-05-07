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
  "17:30"
];

const form = document.querySelector("#tripForm");
const statusMessage = document.querySelector("#status");
const scheduleGrid = document.querySelector("#scheduleGrid");
const suburbLabel = document.querySelector("#suburbLabel");
const toUwcRoutes = document.querySelector("#toUwcRoutes");
const fromUwcRoutes = document.querySelector("#fromUwcRoutes");
const nicknameInput = form.elements.nickname;

renderScheduleGrid();
loadPopularRoutes();
updateSuburbLabel();

form.addEventListener("change", (event) => {
  if (event.target.name === "direction") updateSuburbLabel();
});

nicknameInput.addEventListener("input", () => {
  nicknameInput.value = normalizeNickname(nicknameInput.value);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("", "");

  const formData = new FormData(form);
  const payload = {
    direction: formData.get("direction"),
    area: clean(formData.get("area")),
    schedule: formData.getAll("schedule"),
    nickname: normalizeNickname(formData.get("nickname"))
  };

  if (!isValidNickname(payload.nickname)) {
    setStatus("Use a short nickname with lowercase letters and numbers only.", "error");
    return;
  }

  if (payload.schedule.length === 0) {
    setStatus("Please choose at least one day and time in the schedule.", "error");
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
      setStatus("Those route and time choices were already on the anonymous pilot list.", "success");
    } else if (result.skippedDuplicates > 0) {
      setStatus("New route and time choices were added. Repeated choices were skipped.", "success");
    } else {
      setStatus("You are in the anonymous class pilot list.", "success");
    }
    loadPopularRoutes();
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
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
    renderPopularRoutes(result.routes || []);
  } catch {
    toUwcRoutes.innerHTML = `<p class="empty-routes">Popular routes could not be loaded.</p>`;
    fromUwcRoutes.innerHTML = `<p class="empty-routes">Popular routes could not be loaded.</p>`;
  }
}

function renderPopularRoutes(routes) {
  const toRoutes = routes.filter((route) => route.direction === "to_uwc");
  const fromRoutes = routes.filter((route) => route.direction === "from_uwc");

  renderRouteHeatmap(toUwcRoutes, toRoutes);
  renderRouteHeatmap(fromUwcRoutes, fromRoutes);
}

function renderRouteHeatmap(container, routes) {
  if (routes.length === 0) {
    container.innerHTML = `<p class="empty-routes">No routes yet.</p>`;
    return;
  }

  const suburbs = [...new Set(routes.map((route) => route.area))].sort((a, b) => a.localeCompare(b));
  const schedules = [...new Set(routes.map((route) => route.schedule))].sort(compareSchedules);
  const counts = new Map(routes.map((route) => [`${route.area}|${route.schedule}`, route.interested]));
  const maxInterest = Math.max(...routes.map((route) => route.interested), 1);
  const headerCells = schedules.map((schedule) => `<div class="heatmap-head">${escapeHtml(formatSchedule(schedule))}</div>`).join("");
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

  container.innerHTML = `
    <div class="heatmap-wrap">
      <div class="heatmap-grid" style="grid-template-columns: minmax(128px, 1.2fr) repeat(${schedules.length}, minmax(72px, 1fr));">
        <div class="heatmap-corner">Suburb</div>
        ${headerCells}
        ${rows}
      </div>
    </div>
  `;
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

function normalizeNickname(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "")
    .slice(0, 16);
}

function isValidNickname(value) {
  return /^[a-z0-9]{3,16}$/.test(value);
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
