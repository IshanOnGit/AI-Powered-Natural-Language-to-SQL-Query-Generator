"use strict";

/* Text-to-SQL page.
   It only shows what the API returns and calls the API.
   It never sends SQL (only the question, and later the query_id).
   Nothing is saved in the browser: no local storage, no cookies. */

// ---------------------------------------------------------------
// 1. Small pure helpers (no page access, easy to test on their own)
// ---------------------------------------------------------------

const SQL_WORDS = new Set((
  "select from where group by order having limit offset distinct as on join inner left right full outer cross " +
  "union all intersect except and or not in is null like ilike between exists case when then else end asc desc " +
  "with over partition using true false cast count sum avg min max"
).split(" "));

// Splits SQL into [kind, text] pieces: "kw" | "str" | "num" | "com" | "plain".
function tokenizeSql(sql) {
  const pattern = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+|[\s\S])/g;
  const pieces = [];
  let match;
  while ((match = pattern.exec(sql)) !== null) {
    if (match[1] !== undefined) pieces.push(["com", match[1]]);
    else if (match[2] !== undefined) pieces.push(["str", match[2]]);
    else if (match[3] !== undefined) pieces.push(["num", match[3]]);
    else if (match[4] !== undefined) {
      pieces.push([SQL_WORDS.has(match[4].toLowerCase()) ? "kw" : "plain", match[4]]);
    } else pieces.push(["plain", match[5]]);
  }
  return pieces;
}

// The one place that turns a /generate answer into text for the user.
// Returns {kind: "ready"} or {tone: "info" | "error", title, text}.
function describeOutcome(data) {
  switch (data.status) {
    case "ready":
      return { kind: "ready" };
    case "clarification":
      return { tone: "info", title: "Can you be more specific?", text: data.message };
    case "not_found":
      return { tone: "info", title: "I can't find that in this database", text: data.message };
    case "rejected": {
      const notValid = data.reason === "SYNTAX_ERROR" || data.reason === "EMPTY";
      return notValid
        ? { tone: "error", title: "That query isn't valid",
            text: "The model wrote SQL that isn't valid for this database. Try rephrasing." }
        : { tone: "error", title: "Query blocked",
            text: "This query was blocked because it isn't a safe read-only query." };
    }
    default:
      return { tone: "error", title: "Something went wrong",
               text: (data.message || "The model could not answer.") + " Try again." };
  }
}

// If the query ends in "LIMIT N", returns N (used for the "showing the first N rows" line).
function limitOf(sql) {
  const m = /\blimit\s+(\d+)(?:\s+offset\s+\d+)?\s*;?\s*$/i.exec(sql || "");
  return m ? Number(m[1]) : null;
}

const DEFAULT_PORTS = { postgresql: "5432", mysql: "3306" };
const NICE_NAMES = { postgresql: "PostgreSQL", mysql: "MySQL", sqlite: "SQLite" };
const PRIVACY_LINES = {
  gemini: "Your question and the table and column names are sent to Google.",
  ollama: "Everything stays on this computer.",
};

if (typeof module !== "undefined") {
  module.exports = { tokenizeSql, describeOutcome, limitOf };
}

// ---------------------------------------------------------------
// 2. The page
// ---------------------------------------------------------------

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);

  const state = {
    connected: false,
    providers: [],
    busy: false,
    slip: null,       // {sql, changes, phase: "pending"|"running"|"ran"|"failed", detail}
    queryId: null,    // forgotten on Discard, on Approve, and on any new Generate
  };

  // ---- talking to the API ----
  async function api(path, body) {
    let response;
    try {
      response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new Error("Can't reach the server. Check that it is still running.");
    }
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) {
      const code = data && data.code;
      if (code === "query_unavailable") {
        throw new Error("This query is no longer available. Please generate it again.");
      }
      throw new Error((data && data.message) || "Something went wrong.");
    }
    return data;
  }

  // ---- busy handling: one request at a time ----
  function setBusy(value) { state.busy = value; updateControls(); }

  function updateControls() {
    const canAsk = state.connected;
    $("ask-block").classList.toggle("is-off", !canAsk);
    $("question").disabled = !canAsk;
    $("provider").disabled = !canAsk || state.providers.length === 0;
    $("generate-btn").disabled = !canAsk || state.busy || state.providers.length === 0;
    $("connect-btn").disabled = state.busy;
    $("refresh-btn").disabled = state.busy;
    $("disconnect-btn").disabled = state.busy;
    for (const b of document.querySelectorAll("#outcome button")) b.disabled = state.busy;
  }

  // ---- connection area ----
  function showConnected(info) {
    state.connected = true;
    $("connect-panel").hidden = true;
    $("connected-panel").hidden = false;
    const tables = info.table_count === 1 ? "1 table" : info.table_count + " tables";
    $("connected-summary").textContent = "Connected to " + (NICE_NAMES[info.db_type] || info.db_type) + " · " + tables;
    const list = $("schema-warnings");
    list.textContent = "";
    for (const w of info.warnings || []) {
      const li = document.createElement("li");
      li.textContent = w;
      list.appendChild(li);
    }
    list.hidden = !(info.warnings && info.warnings.length);
    updateControls();
  }

  function showDisconnected() {
    state.connected = false;
    clearOutcome();
    clearResults();
    $("connect-panel").hidden = false;
    $("connected-panel").hidden = true;
    updateControls();
  }

  function showDbFields() {
    const type = $("db-type").value;
    const isFile = type === "sqlite";
    $("server-fields").hidden = isFile;
    $("file-fields").hidden = !isFile;
    if (!isFile) $("port").value = DEFAULT_PORTS[type];
  }
  $("db-type").addEventListener("change", showDbFields);

  $("connect-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const type = $("db-type").value;
    const error = $("connect-error");
    error.hidden = true;

    let body;
    if (type === "sqlite") {
      body = { db_type: type, database: $("file-path").value };
    } else {
      body = {
        db_type: type, host: $("host").value, port: Number($("port").value) || null,
        database: $("database").value, username: $("username").value, password: $("password").value,
      };
    }
    $("password").value = "";          // cleared as soon as the request is sent

    setBusy(true);
    $("connect-btn").textContent = "Connecting…";
    try {
      showConnected(await api("/connect", body));
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    } finally {
      $("connect-btn").textContent = "Connect";
      setBusy(false);
    }
  });

  $("disconnect-btn").addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true);
    try { await api("/disconnect", {}); } catch (e) { /* the page resets either way */ }
    showDisconnected();
    setBusy(false);
  });

  $("refresh-btn").addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true);
    try {
      state.queryId = null;            // the server clears its pending query too
      clearOutcome();
      showConnected(await api("/refresh-schema", {}));
    } catch (e) {
      showMessage({ tone: "error", title: "Couldn't refresh the schema", text: e.message });
    } finally {
      setBusy(false);
    }
  });

  // ---- ask area ----
  function showProviderNote() {
    $("provider-note").textContent = PRIVACY_LINES[$("provider").value] || "";
  }
  $("provider").addEventListener("change", showProviderNote);

  function fillProviders(available) {
    state.providers = available;
    const select = $("provider");
    select.textContent = "";
    if (available.length === 0) {
      const o = document.createElement("option");
      o.textContent = "None available";
      select.appendChild(o);
      $("provider-note").textContent = "No model is set up. Add a Gemini key or start Ollama, then reload this page.";
      return;
    }
    for (const name of available) {
      const o = document.createElement("option");
      o.value = name;
      o.textContent = name === "gemini" ? "Gemini" : name === "ollama" ? "Ollama" : name;
      select.appendChild(o);
    }
    showProviderNote();
  }

  async function generate() {
    if (state.busy || !state.connected) return;
    const question = $("question").value.trim();
    if (!question) {
      showMessage({ tone: "info", title: "Type a question first", text: "Describe what you want to know about your data." });
      return;
    }
    state.queryId = null;
    state.slip = null;
    clearResults();
    setBusy(true);
    $("generate-btn").textContent = "Generating…";
    try {
      const data = await api("/generate", { question, provider: $("provider").value });
      const view = describeOutcome(data);
      if (view.kind === "ready") {
        state.queryId = data.query_id;
        state.slip = { sql: data.sql, changes: data.changes || [], phase: "pending", detail: "" };
        renderSlip(true);
      } else {
        showMessage(view);
      }
    } catch (e) {
      showMessage({ tone: "error", title: "Couldn't generate a query", text: e.message + " Try again." });
    } finally {
      $("generate-btn").textContent = "Generate query";
      setBusy(false);
    }
  }
  $("generate-btn").addEventListener("click", generate);
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      generate();
    }
  });

  // ---- outcome area: messages and the pending slip ----
  function clearOutcome() {
    state.slip = null;
    state.queryId = null;
    $("outcome").hidden = true;
    $("outcome").textContent = "";
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showMessage(view) {
    state.slip = null;
    const box = $("outcome");
    box.textContent = "";
    box.classList.remove("just-shown");
    const note = el("div", "note tone-" + view.tone);
    note.appendChild(el("p", "note-title", view.title));
    note.appendChild(el("p", "", view.text));
    box.appendChild(note);
    box.hidden = false;
    updateControls();
  }

  function sqlBox(sql) {
    const pre = el("pre", "sql-box");
    pre.setAttribute("aria-label", "Generated SQL, read only");
    pre.tabIndex = 0;
    for (const [kind, text] of tokenizeSql(sql)) {
      if (kind === "plain") { pre.appendChild(document.createTextNode(text)); continue; }
      pre.appendChild(el("span", "tok-" + kind, text));
    }
    return pre;
  }

  const SLIP_TITLES = {
    pending: "Nothing has run yet. Review the query, then approve it.",
    running: "Running…",
    ran: "Approved and run",
    failed: "This query did not run to the end",
  };

  function renderSlip(animate) {
    const slip = state.slip;
    const box = $("outcome");
    box.textContent = "";
    box.classList.remove("just-shown");
    if (animate) { void box.offsetWidth; box.classList.add("just-shown"); }

    const status = el("div", "slip-status" + (slip.phase === "ran" ? " is-ran" : slip.phase === "failed" ? " is-failed" : ""));
    status.appendChild(el("span", "dot"));
    status.appendChild(el("span", "", SLIP_TITLES[slip.phase] + (slip.phase === "ran" && slip.detail ? " · " + slip.detail : "")));
    box.appendChild(status);

    box.appendChild(sqlBox(slip.sql));

    if (slip.changes.length) {
      const list = el("ul", "changes");
      for (const c of slip.changes) list.appendChild(el("li", "", c));
      box.appendChild(list);
    }

    if (slip.phase === "failed") {
      const note = el("div", "note tone-error slip-error");
      note.appendChild(el("p", "", slip.detail));
      box.appendChild(note);
    }

    if (slip.phase === "pending" || slip.phase === "running") {
      const actions = el("div", "row slip-actions");
      const approve = el("button", "btn btn-approve", slip.phase === "running" ? "Running…" : "Approve & run");
      approve.type = "button";
      approve.addEventListener("click", approveAndRun);
      const discard = el("button", "btn", "Discard");
      discard.type = "button";
      discard.addEventListener("click", discardQuery);
      actions.append(approve, discard);
      box.appendChild(actions);
    }
    box.hidden = false;
    updateControls();
  }

  function discardQuery() {
    if (state.busy) return;
    clearOutcome();
    clearResults();
  }

  async function approveAndRun() {
    if (state.busy || !state.queryId || !state.slip) return;
    const id = state.queryId;
    state.queryId = null;              // an id can only be used once, so no double run
    state.slip.phase = "running";
    setBusy(true);
    renderSlip(false);
    try {
      const result = await api("/execute", { query_id: id });
      state.slip.phase = "ran";
      state.slip.detail = result.row_count + (result.row_count === 1 ? " row" : " rows") + " in " + result.elapsed_ms + " ms";
      renderResults(result, state.slip.sql);
    } catch (e) {
      state.slip.phase = "failed";
      state.slip.detail = e.message;
      clearResults();
    } finally {
      setBusy(false);
      renderSlip(false);
    }
  }

  // ---- results area ----
  function clearResults() {
    $("results").hidden = true;
    $("results").textContent = "";
  }

  function renderResults(result, sql) {
    const box = $("results");
    box.textContent = "";
    box.appendChild(el("h2", "", "Results"));

    if (!result.rows.length) {
      box.appendChild(el("p", "empty", "No rows returned."));
      box.hidden = false;
      return;
    }

    const isNumber = result.columns.map((_, i) => result.rows.every((r) => r[i] === null || typeof r[i] === "number"));
    const table = el("table");
    const head = el("tr");
    result.columns.forEach((name, i) => head.appendChild(el("th", isNumber[i] ? "num" : "", name)));
    const thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);

    const tbody = el("tbody");
    for (const row of result.rows) {
      const tr = el("tr");
      row.forEach((value, i) => {
        const td = el("td", isNumber[i] ? "num" : "");
        if (value === null) td.appendChild(el("span", "null", "NULL"));
        else td.textContent = String(value);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);

    const wrap = el("div", "table-wrap");
    wrap.tabIndex = 0;
    wrap.setAttribute("aria-label", "Query results");
    wrap.appendChild(table);
    box.appendChild(wrap);

    let meta = result.row_count + (result.row_count === 1 ? " row" : " rows") + " · " + result.elapsed_ms + " ms";
    const limit = limitOf(sql);
    if (limit !== null && result.row_count === limit) meta += ". Showing the first " + limit + " rows.";
    box.appendChild(el("p", "result-meta", meta));
    box.hidden = false;
  }

  // ---- start up: ask the server where things stand ----
  async function start() {
    showDbFields();
    updateControls();
    try {
      const status = await api("/status");
      fillProviders(status.providers_available || []);
      if (status.connected) showConnected({ db_type: status.db_type, table_count: status.table_count, warnings: [] });
    } catch (e) {
      const error = $("connect-error");
      error.textContent = e.message;
      error.hidden = false;
    }
    updateControls();
  }
  start();
}
