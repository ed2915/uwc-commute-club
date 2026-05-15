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

const actionForm = document.querySelector("#actionForm");
const poolLookupForm = document.querySelector("#poolLookupForm");
const poolLookupStatusMessage = document.querySelector("#poolLookupStatus");
const poolLookupResults = document.querySelector("#poolLookupResults");
const actionStatusMessage = document.querySelector("#actionStatus");
const toUwcRoutes = document.querySelector("#toUwcRoutes");
const fromUwcRoutes = document.querySelector("#fromUwcRoutes");
const uniqueUserCount = document.querySelector("#uniqueUserCount");
const studentNumberInput = actionForm.elements.studentNumber;
const addInterestButton = document.querySelector("#addInterest");
const requestConnectionButton = document.querySelector("#requestConnection");
const removeInterestButton = document.querySelector("#removeInterest");
const poolLookupStudentNumberInput = poolLookupForm.elements.studentNumber;
const selectedHeatmapDays = {
  to_uwc: "mon",
  from_uwc: "mon"
};

populateScheduleOptions();
loadPopularRoutes();

studentNumberInput.addEventListener("input", () => {
  studentNumberInput.value = normalizeStudentNumber(studentNumberInput.value);
});

poolLookupStudentNumberInput.addEventListener("input", () => {
  poolLookupStudentNumberInput.value = normalizeStudentNumber(poolLookupStudentNumberInput.value);
});

poolLookupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await lookupStudentPools();
});

addInterestButton.addEventListener("click", () => submitSelectedAction("add"));
requestConnectionButton.addEventListener("click", () => submitSelectedAction("request"));
removeInterestButton.addEventListener("click", () => submitSelectedAction("remove"));

async function submitSelectedAction(action) {
  setActionStatus("", "");

  const payload = selectedRoutePayload();
  const validationError = validateSelectedRoute(payload);
  if (validationError) {
    setActionStatus(validationError, "error");
    return;
  }

  setActionButtonsDisabled(true);

  try {
    if (action === "add") await addSelectedInterest(payload);
    if (action === "request") await requestSelectedConnection(payload);
    if (action === "remove") await removeSelectedInterest(payload);
  } catch (error) {
    setActionStatus(error.message, "error");
  } finally {
    setActionButtonsDisabled(false);
  }
}

async function addSelectedInterest(payload) {
  const response = await fetch("/api/submissions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      schedule: [payload.schedule],
      privacyConsent: true
    })
  });
  const result = await response.json();

  if (!response.ok) {
    throw new Error(result.error || "Could not add you to that route/time group");
  }

  if (result.added === 0) {
    setActionStatus("You were already in that route/time group. You can now request contact with the group.", "success");
  } else {
    setActionStatus("Added you to that route/time group. You can now request contact with the group.", "success");
  }
  loadPopularRoutes();
}

async function requestSelectedConnection(payload) {
  const response = await fetch("/api/connection-requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      connectionConsent: true
    })
  });
  const result = await response.json();

  if (!response.ok) {
    const fallback = response.status === 400
      ? "Add yourself to this exact route/time group before requesting contact."
      : "Could not save your connection request";
    throw new Error(result.error || fallback);
  }

  setActionStatus(`Saved your request. The organiser can review ${result.requested} other route/time interest${result.requested === 1 ? "" : "s"} in that group.`, "success");
}

async function removeSelectedInterest(payload) {
  const response = await fetch("/api/remove-student-number", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      schedule: [payload.schedule]
    })
  });
  const result = await response.json();

  if (!response.ok) {
    throw new Error(result.error || "Could not remove you from that route/time group");
  }

  if (result.deleted > 0) {
    setActionStatus("Removed you from that route/time group.", "success");
    loadPopularRoutes();
  } else {
    setActionStatus("No matching route/time interest was found for that student number.", "success");
  }
}

function selectedRoutePayload() {
  const formData = new FormData(actionForm);
  return {
    direction: formData.get("direction"),
    area: clean(formData.get("area")),
    schedule: formData.get("schedule"),
    studentNumber: normalizeStudentNumber(formData.get("studentNumber")),
    consent: formData.get("consent") === "yes"
  };
}

function validateSelectedRoute(payload) {
  if (!["to_uwc", "from_uwc"].includes(payload.direction)) return "Choose a travel direction.";
  if (!payload.area) return "Choose a suburb.";
  if (!payload.schedule) return "Choose a day and time.";
  if (!isValidStudentNumber(payload.studentNumber)) return "Use a valid 7-digit student number.";
  if (!payload.consent) return "Please consent before continuing.";
  return "";
}

function setActionButtonsDisabled(disabled) {
  addInterestButton.disabled = disabled;
  requestConnectionButton.disabled = disabled;
  removeInterestButton.disabled = disabled;
}

async function lookupStudentPools() {
  setPoolLookupStatus("", "");
  const studentNumber = normalizeStudentNumber(poolLookupStudentNumberInput.value);

  if (!isValidStudentNumber(studentNumber)) {
    setPoolLookupStatus("Use a valid 7-digit student number.", "error");
    return;
  }

  const button = poolLookupForm.querySelector("button");
  button.disabled = true;

  try {
    const response = await fetch(`/api/student-pools?studentNumber=${encodeURIComponent(studentNumber)}`);
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Could not load your pools");
    }

    renderStudentPools(result.pools || []);
    setPoolLookupStatus(result.pools?.length ? "Pools loaded." : "No pools found for that student number.", "success");
  } catch (error) {
    setPoolLookupStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function renderStudentPools(pools) {
  if (pools.length === 0) {
    poolLookupResults.innerHTML = `<p class="empty-routes">No pools found.</p>`;
    return;
  }

  poolLookupResults.innerHTML = pools.map((pool) => `
    <article class="pool-card">
      <div>
        <h3>${escapeHtml(formatPoolRoute(pool))}</h3>
        <p>${escapeHtml(formatSchedule(pool.schedule))}</p>
      </div>
      <dl>
        <div>
          <dt>Status</dt>
          <dd>${escapeHtml(formatPoolStatus(pool))}</dd>
        </div>
        <div>
          <dt>Current interest</dt>
          <dd>${pool.memberCount} student${pool.memberCount === 1 ? "" : "s"}</dd>
        </div>
      </dl>
    </article>
  `).join("");
}

function formatPoolRoute(pool) {
  return pool.direction === "to_uwc" ? `${pool.area} to UWC` : `UWC to ${pool.area}`;
}

function formatPoolStatus(pool) {
  if (pool.status === "0") return "added to group";
  if (pool.status === "1") return "requested connection";
  return pool.status || "pending";
}

function populateScheduleOptions() {
  actionForm.elements.schedule.innerHTML = days
    .flatMap(([day, label]) => timeSlots.map((time) => `<option value="${day}@${time}">${label} ${time}</option>`))
    .join("");
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

  renderRouteTable(toUwcRoutes, toRoutes, "to_uwc");
  renderRouteTable(fromUwcRoutes, fromRoutes, "from_uwc");
}

function renderRouteTable(container, routes, direction) {
  if (routes.length === 0) {
    container.innerHTML = `<p class="empty-routes">No routes yet.</p>`;
    return;
  }

  const selectedDay = selectedHeatmapDays[direction];
  const visibleRoutes = routes.filter((route) => getScheduleDay(route.schedule) === selectedDay);
  const suburbs = [...new Set(visibleRoutes.map((route) => route.area))].sort((a, b) => a.localeCompare(b));
  const schedules = timeSlots.map((time) => `${selectedDay}@${time}`);
  const routesByCell = new Map(visibleRoutes.map((route) => [`${route.area}|${route.schedule}`, route]));
  const counts = new Map(visibleRoutes.map((route) => [`${route.area}|${route.schedule}`, route.interested]));
  const maxInterest = Math.max(...routes.map((route) => route.interested), 1);
  const headerCells = schedules.map((schedule) => `<div class="heatmap-head"><span>${escapeHtml(formatScheduleTime(schedule))}</span></div>`).join("");
  const rows = suburbs.map((suburb) => {
    const cells = schedules.map((schedule) => {
      const count = counts.get(`${suburb}|${schedule}`) || 0;
      const level = count === 0 ? 0 : Math.max(1, Math.ceil((count / maxInterest) * 5));
      const route = routesByCell.get(`${suburb}|${schedule}`);

      if (!route) {
        return `<div class="heatmap-cell heatmap-level-0" aria-label="${escapeHtml(suburb)}, ${escapeHtml(formatSchedule(schedule))}: no interests"></div>`;
      }

      return `<button class="heatmap-cell heatmap-level-${level}" type="button" data-direction="${direction}" data-area="${escapeHtml(suburb)}" data-schedule="${escapeHtml(schedule)}" aria-label="${escapeHtml(suburb)}, ${escapeHtml(formatSchedule(schedule))}: ${count} interested">${count}</button>`;
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
    <div class="route-popup" hidden></div>
    <div class="day-tabs" role="tablist" aria-label="${direction === "to_uwc" ? "To UWC" : "From UWC"} route table day">
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
      renderRouteTable(container, routes, direction);
    });
  });

  container.querySelectorAll(".heatmap-cell[data-schedule]").forEach((button) => {
    button.addEventListener("click", () => {
      const route = routesByCell.get(`${button.dataset.area}|${button.dataset.schedule}`);
      useRouteGroup(container, route);
    });
  });
}

function useRouteGroup(container, route) {
  if (!route) return;

  actionForm.elements.direction.value = route.direction;
  actionForm.elements.area.value = route.area;
  actionForm.elements.schedule.value = route.schedule;
  actionForm.scrollIntoView({ behavior: "smooth", block: "start" });
  setActionStatus(`${formatRoute(route)} selected. Enter your student number, then add yourself or request contact.`, "success");

  const popup = container.querySelector(".route-popup");
  popup.hidden = false;
  popup.innerHTML = `
    <div>
      <strong>${escapeHtml(formatRoute(route))}</strong>
      <span>${escapeHtml(formatSchedule(route.schedule))}</span>
    </div>
    <p>${route.interested} route/time interest${route.interested === 1 ? "" : "s"} in this group.</p>
  `;
}

function formatRoute(route) {
  return route.direction === "to_uwc" ? `${route.area} to UWC` : `UWC to ${route.area}`;
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

function setActionStatus(message, tone) {
  actionStatusMessage.textContent = message;
  actionStatusMessage.dataset.tone = tone;
}

function setPoolLookupStatus(message, tone) {
  poolLookupStatusMessage.textContent = message;
  poolLookupStatusMessage.dataset.tone = tone;
}
