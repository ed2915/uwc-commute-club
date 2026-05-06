import { createServer } from "node:http";
import { appendFile, mkdir, readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const publicDir = join(__dirname, "public");
const dataDir = process.env.DATA_DIR || join(__dirname, "data");
const submissionsFile = join(dataDir, "submissions.csv");
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || (process.env.RENDER ? "0.0.0.0" : "127.0.0.1");

const csvHeaders = [
  "id",
  "submitted_at",
  "direction",
  "area",
  "schedule",
  "nickname",
  "status",
  "matched_group_id",
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

createServer(async (request, response) => {
  try {
    const url = new URL(request.url, `http://${request.headers.host}`);

    if (request.method === "POST" && url.pathname === "/api/submissions") {
      await handleSubmission(request, response);
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/popular-routes") {
      await handlePopularRoutes(response);
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
  }
}

async function handleSubmission(request, response) {
  const payload = await readRequestJson(request);
  const validationError = validateSubmission(payload);

  if (validationError) {
    sendJson(response, 400, { error: validationError });
    return;
  }

  const row = [
    createSubmissionId(),
    new Date().toISOString(),
    payload.direction,
    payload.area,
    payload.schedule.join("|"),
    normalizeNickname(payload.nickname),
    "pending",
    ""
  ].map(csvCell).join(",");

  await appendFile(submissionsFile, `${row}\n`);
  sendJson(response, 201, { ok: true });
}

function validateSubmission(payload) {
  if (!payload || typeof payload !== "object") return "Submission is missing";
  if (!["to_uwc", "from_uwc"].includes(payload.direction)) return "Choose a travel direction";
  if (!isText(payload.area)) return "Choose or enter a starting area";
  if (!Array.isArray(payload.schedule) || payload.schedule.length === 0) return "Choose at least one day and time";
  if (!payload.schedule.every(isScheduleCell)) return "One of the selected schedule times is invalid";
  if (!isValidNickname(payload.nickname)) return "Use a nickname with 3-16 lowercase letters and numbers";
  return "";
}

async function handlePopularRoutes(response) {
  const submissions = await readSubmissions();
  const routeMap = new Map();

  for (const submission of submissions) {
    const area = normalizeArea(submission.area);
    if (submission.status && submission.status !== "pending") continue;
    if (!area || !["to_uwc", "from_uwc"].includes(submission.direction)) continue;

    for (const schedule of submission.schedule.split("|").filter(Boolean)) {
      const key = `${submission.direction}|${area.toLowerCase()}|${schedule}`;
      const route = routeMap.get(key) || {
        direction: submission.direction,
        area,
        schedule,
        start: submission.direction === "to_uwc" ? area : "UWC",
        end: submission.direction === "to_uwc" ? "UWC" : area,
        interested: 0
      };

      route.interested += 1;
      routeMap.set(key, route);
    }
  }

  const routes = [...routeMap.values()]
    .sort((a, b) => b.interested - a.interested || a.start.localeCompare(b.start))
    .slice(0, 8);

  sendJson(response, 200, { routes });
}

function isText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizeNickname(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "")
    .slice(0, 16);
}

function isValidNickname(value) {
  return /^[a-z0-9]{3,16}$/.test(normalizeNickname(value));
}

function isScheduleCell(value) {
  return typeof value === "string" && /^(mon|tue|wed|thu|fri)@\d{2}:\d{2}$/.test(value);
}

function createSubmissionId() {
  return `sub_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
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
