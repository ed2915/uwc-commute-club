import { createServer } from "node:http";
import { appendFile, mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const publicDir = join(__dirname, "public");
const dataDir = process.env.DATA_DIR || join(__dirname, "data");
const submissionsFile = join(dataDir, "submissions.csv");
const connectionRequestsFile = join(dataDir, "connection_requests.csv");
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || (process.env.RENDER ? "0.0.0.0" : "127.0.0.1");
const adminToken = process.env.ADMIN_TOKEN || "";

const csvHeaders = [
  "id",
  "submitted_at",
  "direction",
  "area",
  "schedule",
  "student_number",
  "status",
  "matched_group_id",
];

const connectionRequestHeaders = [
  "id",
  "requested_at",
  "student_number",
  "direction",
  "area",
  "schedule",
  "requested_member_labels",
  "requested_submission_ids",
  "status",
  "notes",
];

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml"
};

await mkdir(dataDir, { recursive: true });
await ensureCsvFile();
await ensureConnectionRequestsFile();

createServer(async (request, response) => {
  try {
    const url = new URL(request.url, `http://${request.headers.host}`);

    if (request.method === "POST" && url.pathname === "/api/submissions") {
      await handleSubmission(request, response);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/remove-student-number") {
      await handleRemoveStudentNumber(request, response);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/connection-requests") {
      await handleConnectionRequest(request, response);
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/popular-routes") {
      await handlePopularRoutes(response);
      return;
    }

    if (url.pathname === "/api/admin/submissions") {
      await handleAdminSubmissions(request, response);
      return;
    }

    if (url.pathname === "/api/admin/connection-requests") {
      await handleAdminConnectionRequests(request, response);
      return;
    }

    if (url.pathname.startsWith("/api/admin/submissions/")) {
      await handleAdminSubmission(request, response, url.pathname);
      return;
    }

    if (request.method === "GET" && url.pathname === "/health") {
      sendJson(response, 200, { ok: true });
      return;
    }

    if (request.method === "GET") {
      await serveStatic(url.pathname, response);
      return;
    }

    sendJson(response, 405, { error: "Method not allowed" });
  } catch (error) {
    console.error(error);
    sendJson(response, 500, { error: "Something went wrong" });
  }
}).listen(port, host, () => {
  const displayHost = host === "0.0.0.0" ? "localhost" : host;
  console.log(`UWC Commute Club is running at http://${displayHost}:${port}`);
  console.log(`Saving submissions to ${submissionsFile}`);
});

async function ensureCsvFile() {
  try {
    await stat(submissionsFile);
  } catch {
    await appendFile(submissionsFile, `${csvHeaders.join(",")}\n`);
    return;
  }

  const submissions = await readSubmissions();
  await writeSubmissions(submissions);
}

async function ensureConnectionRequestsFile() {
  try {
    await stat(connectionRequestsFile);
  } catch {
    await appendFile(connectionRequestsFile, `${connectionRequestHeaders.join(",")}\n`);
  }
}

async function handleSubmission(request, response) {
  const payload = await readRequestJson(request);
  const validationError = validateSubmission(payload);

  if (validationError) {
    sendJson(response, 400, { error: validationError });
    return;
  }

  const submissions = await readSubmissions();
  const studentNumber = normalizeStudentNumber(payload.studentNumber);
  const area = normalizeArea(payload.area);
  const existingKeys = new Set(submissions.flatMap(submissionInterestKeys));
  const submittedAt = new Date().toISOString();
  const rows = [...new Set(payload.schedule)]
    .filter((schedule) => !existingKeys.has(interestKey({
      direction: payload.direction,
      area,
      schedule,
      student_number: studentNumber
    })))
    .map((schedule) => [
      createSubmissionId(),
      submittedAt,
      payload.direction,
      area,
      schedule,
      studentNumber,
      "pending",
      ""
    ].map(csvCell).join(","));

  if (rows.length > 0) {
    await appendFile(submissionsFile, `${rows.join("\n")}\n`);
  }

  sendJson(response, 201, {
    ok: true,
    added: rows.length,
    skippedDuplicates: payload.schedule.length - rows.length
  });
}

function validateSubmission(payload) {
  if (!payload || typeof payload !== "object") return "Submission is missing";
  if (!["to_uwc", "from_uwc"].includes(payload.direction)) return "Choose a travel direction";
  if (!isText(payload.area)) return "Choose or enter a starting area";
  if (!Array.isArray(payload.schedule) || payload.schedule.length === 0) return "Choose at least one day and time";
  if (!payload.schedule.every(isScheduleCell)) return "One of the selected schedule times is invalid";
  if (!isValidStudentNumber(payload.studentNumber)) return "Use a valid 7-digit student number";
  if (payload.privacyConsent !== true) return "Consent is required to collect your student number";
  return "";
}

async function handleRemoveStudentNumber(request, response) {
  const payload = await readRequestJson(request);

  if (!isValidStudentNumber(payload.studentNumber)) {
    sendJson(response, 400, { error: "Use a valid 7-digit student number" });
    return;
  }

  const studentNumber = normalizeStudentNumber(payload.studentNumber);
  const direction = payload.direction;
  const area = normalizeArea(payload.area);
  const schedulesToRemove = new Set(Array.isArray(payload.schedule) ? payload.schedule : []);

  if (!["to_uwc", "from_uwc"].includes(direction)) {
    sendJson(response, 400, { error: "Choose a travel direction" });
    return;
  }

  if (!area) {
    sendJson(response, 400, { error: "Choose a suburb" });
    return;
  }

  if (schedulesToRemove.size === 0 || ![...schedulesToRemove].every(isScheduleCell)) {
    sendJson(response, 400, { error: "Choose at least one day and time to remove" });
    return;
  }

  const submissions = await readSubmissions();
  let deleted = 0;
  const nextSubmissions = submissions
    .map((submission) => {
      const isTarget =
        identityKey(submission.student_number) === studentNumber &&
        submission.direction === direction &&
        normalizeArea(submission.area).toLowerCase() === area.toLowerCase();

      if (!isTarget) return submission;

      const remainingSchedule = scheduleCells(submission)
        .filter((schedule) => !schedulesToRemove.has(schedule));
      const removedCount = scheduleCells(submission).length - remainingSchedule.length;
      deleted += removedCount;

      return { ...submission, schedule: remainingSchedule.join("|") };
    })
    .filter((submission) => scheduleCells(submission).length > 0);

  if (deleted > 0) {
    await writeSubmissions(nextSubmissions);
  }

  sendJson(response, 200, { ok: true, deleted });
}

async function handleConnectionRequest(request, response) {
  const payload = await readRequestJson(request);
  const validationError = validateConnectionRequest(payload);

  if (validationError) {
    sendJson(response, 400, { error: validationError });
    return;
  }

  const studentNumber = normalizeStudentNumber(payload.studentNumber);
  const area = normalizeArea(payload.area);
  const labels = normalizeMemberLabels(payload.memberLabels);
  const submissions = await readSubmissions();
  const group = routeGroupMembers(submissions, payload.direction, area, payload.schedule);
  const requesterIsInGroup = group.some((member) => identityKey(member.student_number) === studentNumber);

  if (!requesterIsInGroup) {
    sendJson(response, 400, { error: "Your student number must already be in that exact route/time group before you can request a connection." });
    return;
  }

  const selectedMembers = labels
    .map((label) => group.find((member) => member.label === label))
    .filter(Boolean)
    .filter((member) => identityKey(member.student_number) !== studentNumber);

  if (selectedMembers.length === 0) {
    sendJson(response, 400, { error: "Choose at least one other group number." });
    return;
  }

  const missingLabels = labels.some((label) => !group.find((member) => member.label === label));
  if (missingLabels) {
    sendJson(response, 400, { error: "One or more selected group numbers are not in that route/time group." });
    return;
  }

  const row = [
    createConnectionRequestId(),
    new Date().toISOString(),
    studentNumber,
    payload.direction,
    area,
    payload.schedule,
    labels.join("|"),
    selectedMembers.map((member) => member.submissionId).join("|"),
    "pending",
    ""
  ].map(csvCell).join(",");

  await appendFile(connectionRequestsFile, `${row}\n`);
  sendJson(response, 201, { ok: true, requested: selectedMembers.length });
}

function validateConnectionRequest(payload) {
  if (!payload || typeof payload !== "object") return "Connection request is missing";
  if (!isValidStudentNumber(payload.studentNumber)) return "Use a valid 7-digit student number";
  if (!["to_uwc", "from_uwc"].includes(payload.direction)) return "Choose a travel direction";
  if (!isText(payload.area)) return "Choose a suburb";
  if (!isScheduleCell(payload.schedule)) return "Choose a valid day and time";
  if (normalizeMemberLabels(payload.memberLabels).length === 0) return "Enter at least one group number";
  if (payload.connectionConsent !== true) return "Consent is required before requesting a connection";
  return "";
}

async function handlePopularRoutes(response) {
  const submissions = await readSubmissions();
  const routeMap = new Map();
  const countedKeys = new Set();

  for (const submission of submissions) {
    const area = normalizeArea(submission.area);
    if (submission.status && submission.status !== "pending") continue;
    if (!area || !["to_uwc", "from_uwc"].includes(submission.direction)) continue;

    for (const schedule of scheduleCells(submission)) {
      const countedKey = interestKey({ ...submission, area, schedule });
      if (countedKeys.has(countedKey)) continue;
      countedKeys.add(countedKey);

      const key = `${submission.direction}|${area.toLowerCase()}|${schedule}`;
      const route = routeMap.get(key) || {
        direction: submission.direction,
        area,
        schedule,
        start: submission.direction === "to_uwc" ? area : "UWC",
        end: submission.direction === "to_uwc" ? "UWC" : area,
        interested: 0,
        members: []
      };

      route.interested += 1;
      route.members.push({
        label: route.members.length + 1
      });
      routeMap.set(key, route);
    }
  }

  const routes = [...routeMap.values()]
    .sort((a, b) => b.interested - a.interested || a.start.localeCompare(b.start));

  sendJson(response, 200, { routes, uniqueUsers: countedKeys.size });
}

async function handleAdminSubmissions(request, response) {
  if (!authorizeAdmin(request, response)) return;

  if (request.method !== "GET") {
    sendJson(response, 405, { error: "Method not allowed" });
    return;
  }

  const submissions = await readSubmissions();
  sendJson(response, 200, { submissions });
}

async function handleAdminConnectionRequests(request, response) {
  if (!authorizeAdmin(request, response)) return;

  if (request.method !== "GET") {
    sendJson(response, 405, { error: "Method not allowed" });
    return;
  }

  const requests = await readConnectionRequests();
  sendJson(response, 200, { requests });
}

async function handleAdminSubmission(request, response, pathname) {
  if (!authorizeAdmin(request, response)) return;

  const id = decodeURIComponent(pathname.replace("/api/admin/submissions/", ""));
  if (!id) {
    sendJson(response, 400, { error: "Submission id is required" });
    return;
  }

  if (request.method === "DELETE") {
    const submissions = await readSubmissions();
    const nextSubmissions = submissions.filter((submission) => submission.id !== id);

    if (nextSubmissions.length === submissions.length) {
      sendJson(response, 404, { error: "Submission not found" });
      return;
    }

    await writeSubmissions(nextSubmissions);
    sendJson(response, 200, { ok: true, deleted: id });
    return;
  }

  if (request.method === "PATCH") {
    const payload = await readRequestJson(request);
    const validationError = validateAdminPatch(payload);

    if (validationError) {
      sendJson(response, 400, { error: validationError });
      return;
    }

    const submissions = await readSubmissions();
    const index = submissions.findIndex((submission) => submission.id === id);

    if (index === -1) {
      sendJson(response, 404, { error: "Submission not found" });
      return;
    }

    submissions[index] = { ...submissions[index], ...payload };
    await writeSubmissions(submissions);
    sendJson(response, 200, { ok: true, submission: submissions[index] });
    return;
  }

  sendJson(response, 405, { error: "Method not allowed" });
}

function authorizeAdmin(request, response) {
  if (!adminToken) {
    sendJson(response, 404, { error: "Not found" });
    return false;
  }

  const header = request.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : "";

  if (token !== adminToken) {
    sendJson(response, 401, { error: "Unauthorized" });
    return false;
  }

  return true;
}

function validateAdminPatch(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return "Patch body must be an object";

  const allowedFields = new Set(["direction", "area", "schedule", "student_number", "status", "matched_group_id"]);
  const fields = Object.keys(payload);
  if (fields.length === 0) return "Patch body is empty";
  if (!fields.every((field) => allowedFields.has(field))) return "Patch contains an unsupported field";

  if ("direction" in payload && !["to_uwc", "from_uwc"].includes(payload.direction)) return "Direction is invalid";
  if ("area" in payload && !isText(payload.area)) return "Area is invalid";
  if ("schedule" in payload && !String(payload.schedule).split("|").filter(Boolean).every(isScheduleCell)) return "Schedule is invalid";
  if ("student_number" in payload && !isValidStudentNumber(payload.student_number)) return "Student number is invalid";
  if ("status" in payload && !["pending", "matched", "deleted", "archived"].includes(payload.status)) return "Status is invalid";

  for (const field of fields) {
    payload[field] = String(payload[field] || "").trim();
  }

  if ("student_number" in payload) payload.student_number = normalizeStudentNumber(payload.student_number);
  if ("area" in payload) payload.area = normalizeArea(payload.area);

  return "";
}

function isText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizeStudentNumber(value) {
  return String(value || "")
    .replace(/\D/g, "")
    .slice(0, 7);
}

function isValidStudentNumber(value) {
  return /^\d{7}$/.test(String(value || "").replace(/\D/g, ""));
}

function isScheduleCell(value) {
  return typeof value === "string" && /^(mon|tue|wed|thu|fri)@\d{2}:\d{2}$/.test(value);
}

function normalizeMemberLabels(value) {
  const values = Array.isArray(value) ? value : String(value || "").split(/[\s,;#]+/);
  return [...new Set(values
    .map((item) => Number(String(item).replace(/\D/g, "")))
    .filter((item) => Number.isInteger(item) && item > 0))];
}

function scheduleCells(submission) {
  return String(submission.schedule || "").split("|").filter(Boolean);
}

function submissionInterestKeys(submission) {
  if (["deleted", "archived"].includes(submission.status)) return [];

  return scheduleCells(submission).map((schedule) => interestKey({ ...submission, schedule }));
}

function routeGroupMembers(submissions, direction, area, schedule) {
  const members = [];
  const seen = new Set();

  for (const submission of submissions) {
    if (submission.status && submission.status !== "pending") continue;
    if (submission.direction !== direction) continue;
    if (normalizeArea(submission.area).toLowerCase() !== normalizeArea(area).toLowerCase()) continue;
    if (!scheduleCells(submission).includes(schedule)) continue;

    const key = interestKey({ ...submission, area, schedule });
    if (seen.has(key)) continue;
    seen.add(key);
    members.push({
      label: members.length + 1,
      submissionId: submission.id,
      student_number: submission.student_number
    });
  }

  return members;
}

function interestKey(submission) {
  return [
    submission.direction,
    normalizeArea(submission.area).toLowerCase(),
    submission.schedule,
    submissionIdentityKey(submission)
  ].join("|");
}

function submissionIdentityKey(submission) {
  return identityKey(submission.student_number) || identityKey(submission.pilot_code) || identityKey(submission.nickname) || identityKey(submission.id);
}

function identityKey(value) {
  const text = String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return text;
}

function createSubmissionId() {
  return `sub_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function createConnectionRequestId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

async function readSubmissions() {
  const csv = await readFile(submissionsFile, "utf8");
  const [headerLine, ...lines] = csv.trim().split("\n");
  if (!headerLine) return [];

  const headers = parseCsvLine(headerLine);
  return lines
    .filter(Boolean)
    .map((line) => {
      const values = parseCsvLine(line);
      const submission = Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
      if (!submission.student_number) {
        submission.student_number = submission.pilot_code || submission.nickname || "";
      }
      delete submission.pilot_code;
      delete submission.nickname;
      return submission;
    });
}

async function writeSubmissions(submissions) {
  const lines = [
    csvHeaders.join(","),
    ...submissions.map((submission) => csvHeaders
      .map((header) => csvCell(submission[header] || ""))
      .join(","))
  ];
  const tempFile = `${submissionsFile}.tmp`;
  await writeFile(tempFile, `${lines.join("\n")}\n`);
  await rename(tempFile, submissionsFile);
}

async function readConnectionRequests() {
  const csv = await readFile(connectionRequestsFile, "utf8");
  const [headerLine, ...lines] = csv.trim().split("\n");
  if (!headerLine) return [];

  const headers = parseCsvLine(headerLine);
  return lines
    .filter(Boolean)
    .map((line) => {
      const values = parseCsvLine(line);
      return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
    });
}

function parseCsvLine(line) {
  const cells = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    const next = line[index + 1];

    if (char === '"' && quoted && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }

  cells.push(cell);
  return cells;
}

function normalizeArea(area) {
  return String(area || "")
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function csvCell(value) {
  const text = String(value).replaceAll('"', '""');
  return `"${text}"`;
}

async function readRequestJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks).toString("utf8");
  return JSON.parse(body || "{}");
}

async function serveStatic(pathname, response) {
  const requestedPath = pathname === "/" ? "/index.html" : pathname;
  const safePath = normalize(decodeURIComponent(requestedPath)).replace(/^(\.\.[/\\])+/, "");
  const filePath = join(publicDir, safePath);

  if (!filePath.startsWith(publicDir)) {
    sendText(response, 403, "Forbidden");
    return;
  }

  try {
    const file = await readFile(filePath);
    response.writeHead(200, {
      "Content-Type": contentTypes[extname(filePath)] || "application/octet-stream"
    });
    response.end(file);
  } catch {
    sendText(response, 404, "Not found");
  }
}

function sendJson(response, status, data) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(data));
}

function sendText(response, status, text) {
  response.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(text);
}
