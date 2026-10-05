// No browser dependencies: parsing and conversion are shared by the UI and checks.
export function parseCSV(text) {
  text = text.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], cell = "", quoted = false, closed = false;
  const field = () => { row.push(cell); cell = ""; closed = false; };
  const record = () => {
    field();
    if (row.some(value => value.trim() !== "")) rows.push(row);
    row = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') { quoted = false; closed = true; }
      else cell += c;
    } else if (c === ",") field();
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      record();
    } else if (c === '"' && cell === "" && !closed) quoted = true;
    else if (closed || c === '"') throw new Error("Invalid CSV quoting. Quote cells containing commas or quotation marks.");
    else cell += c;
  }
  if (quoted) throw new Error("A quoted CSV cell is not closed.");
  if (cell || row.length || closed) record();
  if (rows.length < 2) throw new Error("Include a header and at least one data row.");
  const headers = rows.shift().map(h => h.trim());
  if (headers.some(h => !h) || new Set(headers.map(h => h.toLowerCase())).size !== headers.length) {
    throw new Error("Column names must be nonempty and unique.");
  }
  rows.forEach((r, i) => {
    if (r.length !== headers.length) throw new Error(`Data row ${i + 1} has ${r.length} columns; expected ${headers.length}.`);
  });
  return { headers, rows };
}

export function guessColumn(headers, type) {
  const patterns = type === "email"
    ? [/^(uid|email|email address)$/i, /sis login/i, /login id/i, /email/i]
    : [/^section$/i, /section/i, /^(multiplier|accommodation|label|group)$/i];
  for (const pattern of patterns) {
    const index = headers.findIndex(h => pattern.test(h));
    if (index !== -1) return index;
  }
  return -1;
}

export function plLabelColumns(headers) {
  return headers.flatMap((name, index) => /^label[1-9]\d*$/i.test(name) ? [index] : []);
}

export function validEmail(value) {
  // Deliberately reject spreadsheet formula prefixes and incomplete Canvas login IDs.
  return !/^[=+\-@]/.test(value) && /^[^\s@,;<>"()]+@[^\s@,;<>"()]+\.[^\s@,;<>"()]+$/.test(value);
}

export function buildRoster(primary, extra = null) {
  const students = new Map(), issues = [];
  let duplicates = 0;
  function ingest(source, supplemental) {
    if (!source) return;
    const { table, emailColumn, groupColumn, mappings } = source;
    if (emailColumn < 0) throw new Error("Choose the email / UID column.");
    const labelsColumns = plLabelColumns(table.headers);
    const readyCSV = source.readyCSV;
    if (!readyCSV && (groupColumn < 0 || emailColumn === groupColumn)) {
      throw new Error("Choose different columns for email and section / group.");
    }
    for (const [i, row] of table.rows.entries()) {
      const uid = row[emailColumn].trim();
      const rawLabels = readyCSV ? labelsColumns.map(n => row[n].trim()).filter(Boolean)
        : [mappings.get(row[groupColumn].trim())?.trim() || ""].filter(Boolean);
      let reason = "";
      if (!validEmail(uid)) reason = "Missing or invalid email / UID";
      else if (!rawLabels.length) reason = "No label assigned";
      else if (rawLabels.some(l => l.length > 255 || /[\r\n]/.test(l))) reason = "Label must be one line and at most 255 characters";
      else if (supplemental && !students.has(uid)) reason = "Email is not in the primary roster (not added automatically)";
      if (reason) { issues.push({ source: source.name, row: i + 1, uid, reason }); continue; }
      if (students.has(uid) && !supplemental) duplicates++;
      if (!students.has(uid)) students.set(uid, new Set());
      for (const label of rawLabels) students.get(uid).add(label);
    }
  }
  ingest(primary, false);
  ingest(extra, true);
  const records = [...students].map(([uid, labels]) => ({ uid, labels: [...labels].sort() }))
    .sort((a, b) => a.uid.localeCompare(b.uid));
  const labels = [...new Set(records.flatMap(r => r.labels))].sort();
  if (records.length > 5000) throw new Error("PrairieLearn accepts at most 5,000 students per synchronization. Split the roster first.");
  if (labels.length > 100) throw new Error("This export uses more than 100 labels, the course-instance label limit.");
  return { records, labels, issues, duplicates };
}

export function writeCSV(records) {
  if (!records.length) return "";
  const width = Math.max(...records.map(r => r.labels.length));
  const quote = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
  const rows = [["uid", ...Array.from({ length: width }, (_, i) => `label${i + 1}`)],
    ...records.map(r => [r.uid, ...r.labels, ...Array(width - r.labels.length).fill("")])];
  return rows.map(row => row.map(quote).join(",")).join("\r\n") + "\r\n";
}
