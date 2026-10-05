import { parseCSV, guessColumn, plLabelColumns, buildRoster, writeCSV } from "./core.mjs";

const $ = id => document.getElementById(id);
const state = { roster: null, extra: null, definitions: null, errors: new Map(), result: null, page: 0 };
const revisions = { roster: 0, extra: 0, definitions: 0 };
const PAGE_SIZE = 50;
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
};

function invalidate() {
  $("confirm").checked = false;
  $("skip-issues").checked = false;
  $("export-status").textContent = "";
  state.page = 0;
}

async function readFile(file) {
  if (file.size > 10 * 1024 * 1024) throw new Error("Choose a file smaller than 10 MB.");
  const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  return text;
}

for (const kind of ["roster", "extra"]) {
  $(`${kind}-file`).addEventListener("change", async event => {
    const file = event.target.files[0];
    const revision = ++revisions[kind];
    state[kind] = null;
    state.errors.delete(kind);
    invalidate();
    $(`${kind}-name`).textContent = file?.name || "No file selected";
    // Block exports while asynchronous reads are pending.
    if (file) state.errors.set(kind, "Reading file…");
    renderSources(); renderResult();
    if (!file) return;
    try {
      const table = parseCSV(await readFile(file));
      if (revision !== revisions[kind]) return;
      const readyCSV = table.headers.some(h => h.toLowerCase() === "uid") && plLabelColumns(table.headers).length > 0;
      state[kind] = { table, name: file.name, emailColumn: guessColumn(table.headers, "email"),
        groupColumn: guessColumn(table.headers, "group"), mappings: new Map(), readyCSV };
      state.errors.delete(kind);
    } catch (error) {
      if (revision !== revisions[kind]) return;
      state.errors.set(kind, `${file.name}: ${error.message}`);
    }
    renderSources(); renderResult();
  });
}

function selectColumn(source, kind, type) {
  const label = node("label", type === "email" ? "Email / UID column" : "Section / group column");
  const select = node("select");
  select.id = `${kind}-${type}`;
  const placeholder = node("option", "Choose a column…"); placeholder.value = "-1"; select.append(placeholder);
  source.table.headers.forEach((h, i) => { const option = node("option", h); option.value = String(i); select.append(option); });
  select.value = String(source[type === "email" ? "emailColumn" : "groupColumn"]);
  select.addEventListener("change", () => {
    source[type === "email" ? "emailColumn" : "groupColumn"] = Number(select.value);
    if (type === "group") source.mappings = new Map();
    invalidate(); renderSources(); renderResult();
  });
  label.append(select);
  return label;
}

function renderSources() {
  $("sources").replaceChildren();
  $("mapping-empty").hidden = Boolean(state.roster || state.extra);
  $("remove-extra").hidden = !$("extra-file").files.length;
  for (const kind of ["roster", "extra"]) {
    const source = state[kind];
    if (!source) continue;
    const block = node("div", undefined, "source-block");
    block.append(node("p", `${kind === "roster" ? "Primary roster" : "Additional labels"} · ${source.name}`, "source-name"));
    const columns = node("div", undefined, "columns");
    columns.append(selectColumn(source, kind, "email"));
    if (!source.readyCSV) columns.append(selectColumn(source, kind, "group"));
    block.append(columns);
    if (source.readyCSV) {
      block.append(node("p", "PL CSV detected. All label1, label2, … columns will be combined for each email.", "source-name"));
    } else if (source.groupColumn >= 0) {
      const heading = node("div", undefined, "mapping-row mapping-heading");
      heading.append(node("span", "SOURCE VALUE"), node("span", ""), node("span", "EXISTING PL LABEL"));
      block.append(heading);
      const list = node("div", undefined, "mapping-list");
      const groups = [...new Set(source.table.rows.map(r => r[source.groupColumn].trim()).filter(Boolean))].sort();
      // Avoid silently splitting Canvas section strings or guessing student identities.
      groups.forEach((group, index) => {
        if (!source.mappings.has(group)) source.mappings.set(group, group);
        const line = node("div", undefined, "mapping-row");
        const label = node("label", group);
        const input = node("input"); input.type = "text"; input.maxLength = 255;
        input.id = `${kind}-label-${index}`; label.htmlFor = input.id;
        input.value = source.mappings.get(group);
        input.setAttribute("aria-label", `PL label for ${group}`);
        input.addEventListener("input", () => { source.mappings.set(group, input.value); invalidate(); renderResult(); });
        line.append(label, node("span", "→"), input); list.append(line);
      });
      block.append(list);
    }
    $("sources").append(block);
  }
}

$("definitions-file").addEventListener("change", async event => {
  const file = event.target.files[0], revision = ++revisions.definitions;
  state.definitions = null; state.errors.delete("definitions"); invalidate();
  $("definitions-status").textContent = "";
  $("remove-definitions").hidden = !file;
  if (file) state.errors.set("definitions", "Reading label definitions…");
  renderResult();
  if (!file) return;
  try {
    const data = JSON.parse(await readFile(file));
    if (revision !== revisions.definitions) return;
    if (!Array.isArray(data.studentLabels) || data.studentLabels.some(l => !l || typeof l.name !== "string")) {
      throw new Error("Expected infoCourseInstance.json with a studentLabels array containing label names.");
    }
    state.definitions = new Set(data.studentLabels.map(l => l.name));
    state.errors.delete("definitions");
    $("definitions-status").textContent = `${state.definitions.size} label names loaded from ${file.name}.`;
  } catch (error) {
    if (revision !== revisions.definitions) return;
    state.errors.set("definitions", error.message);
  }
  renderResult();
});

$("remove-extra").addEventListener("click", () => {
  revisions.extra++; state.extra = null; state.errors.delete("extra");
  $("extra-file").value = ""; $("extra-name").textContent = "No file selected";
  invalidate(); renderSources(); renderResult();
});
$("remove-definitions").addEventListener("click", () => {
  revisions.definitions++; state.definitions = null; state.errors.delete("definitions");
  $("definitions-file").value = ""; $("definitions-status").textContent = "";
  $("remove-definitions").hidden = true; invalidate(); renderResult();
});

function renderResult() {
  state.result = null;
  $("file-error").hidden = state.errors.size === 0;
  $("file-error").textContent = [...state.errors.values()].join(" · ");
  $("conversion-error").hidden = true;
  if (state.roster && !state.errors.size) {
    try {
      const result = buildRoster(state.roster, state.extra);
      if (state.definitions) {
        const missing = result.labels.filter(l => !state.definitions.has(l));
        if (missing.length) throw new Error(`Labels not found in the course file: ${missing.join(", ")}. Match names exactly or create them in PL first.`);
      }
      state.result = result;
    } catch (error) {
      $("conversion-error").textContent = error.message;
      $("conversion-error").hidden = false;
    }
  }
  const result = state.result;
  $("student-total").textContent = result?.records.length || 0;
  $("label-total").textContent = result?.labels.length || 0;
  $("duplicate-note").textContent = result?.duplicates ? `${result.duplicates} repeated roster rows merged by email.` : "One row per student.";
  $("label-summary").replaceChildren();
  for (const label of result?.labels || []) {
    const line = node("div");
    line.append(node("span", label), node("b", String(result.records.filter(r => r.labels.includes(label)).length)));
    $("label-summary").append(line);
  }
  $("issues-panel").hidden = !result?.issues.length;
  $("issues-title").textContent = `${result?.issues.length || 0} row(s) need attention`;
  $("issues").replaceChildren();
  for (const issue of result?.issues || []) {
    const row = node("tr"); row.append(node("td", `${issue.source} · data row ${issue.row}`), node("td", issue.uid || "—"), node("td", issue.reason));
    $("issues").append(row);
  }
  renderPreview(); updateExport();
}

function renderPreview() {
  const query = $("search").value.trim().toLowerCase();
  const records = (state.result?.records || []).filter(r => `${r.uid} ${r.labels.join(" ")}`.toLowerCase().includes(query));
  const pages = Math.max(1, Math.ceil(records.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  $("preview-count").textContent = `${records.length} students${query ? " matched" : ""}`;
  $("page-number").textContent = `Page ${state.page + 1} of ${pages}`;
  $("prev").disabled = state.page === 0; $("next").disabled = state.page >= pages - 1;
  $("preview-body").replaceChildren();
  for (const record of records.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE)) {
    const row = node("tr"), labels = node("td");
    record.labels.forEach(label => labels.append(node("span", label, "tag")));
    row.append(node("td", record.uid), labels); $("preview-body").append(row);
  }
  if (!records.length) {
    const row = node("tr"), cell = node("td", query ? "No matching students." : "Your converted roster will appear here.", "empty");
    cell.colSpan = 2; row.append(cell); $("preview-body").append(row);
  }
}

function canExport() {
  return Boolean(state.result?.records.length && !state.errors.size && $("confirm").checked &&
    (!state.result.issues.length || $("skip-issues").checked));
}
function updateExport() { $("download").disabled = $("copy").disabled = !canExport(); }
$("confirm").addEventListener("change", updateExport);
$("skip-issues").addEventListener("change", updateExport);
$("search").addEventListener("input", () => { state.page = 0; renderPreview(); });
$("prev").addEventListener("click", () => { state.page--; renderPreview(); });
$("next").addEventListener("click", () => { state.page++; renderPreview(); });
$("download").addEventListener("click", () => {
  if (!canExport()) return;
  const url = URL.createObjectURL(new Blob([writeCSV(state.result.records)], { type: "text/csv;charset=utf-8" }));
  const a = node("a"); a.href = url; a.download = "prairielearn-student-labels.csv";
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  $("export-status").textContent = `Exported ${state.result.records.length} students. Review PL's Compare screen before applying.`;
});
$("copy").addEventListener("click", async () => {
  if (!canExport()) return;
  try {
    await navigator.clipboard.writeText(writeCSV(state.result.records));
    $("export-status").textContent = "CSV copied. Paste it into PL's Student CSV field, then Compare.";
  } catch {
    $("export-status").textContent = "Clipboard access was unavailable. Download the CSV instead.";
  }
});
$("reset").addEventListener("click", () => location.reload());
