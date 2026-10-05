# Roster Bridge — Chrome / Edge extension

Independent Manifest V3 extension. The Streamlit application and Live CCTV are
unchanged. No build step, server, GitHub token, PrairieLearn token, or npm packages
are needed. Clicking the toolbar action opens an extension-owned tab.

## Install locally

1. Open `chrome://extensions` (Chrome) or `edge://extensions` (Edge).
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this `chrome-extension` directory.
4. Pin **PrairieLearn Roster Bridge** and click its icon.

After updating the files, click **Reload** on the extension card and reopen its tab.
This is an unpacked extension, not a Chrome Web Store or Edge Add-ons publication.

## Convert a roster

1. Select a UTF-8 Canvas CSV. Choose its email/UID and section columns. Canvas
   login IDs must already be full email addresses: no domain is guessed.
2. Map each section value to its exact existing PrairieLearn label name.
   Commas in a section name are preserved; the tool never guesses how to split
   combined sections. Repeated student rows merge all their labels.
3. Optionally select an SDC/additional-label CSV. It can contain email + group
   (for example, `Email,Multiplier`) with an editable label mapping, or PL-format
   `uid,label1,label2,...`. Match by email only; names are never fuzzy-matched.
   Additional-file students absent from the primary roster are flagged and are
   not silently added. A label named `SDC 1.5x` is just a label: configure actual
   duration and deadline overrides in each assessment yourself.
4. Optionally choose `infoCourseInstance.json` to validate against its
   `studentLabels` names. The tool does not modify or sync that file.
5. Review every row issue. Invalid emails, blank labels and unmatched additional
   rows block export unless you explicitly accept exporting only valid rows.
   Canvas gradebook metadata rows such as “Points Possible” are reported here;
   review them rather than silently discarding arbitrary rows.
6. Confirm the complete label sets, then **Copy CSV** or **Download PL CSV**.
   The preview filter and pagination do not restrict the exported roster.

## Apply in PrairieLearn

Create the labels first (UI or `infoCourseInstance.json` + sync), then open:

**Students → Manage enrollments → Synchronize student list → CSV**

The official CSV synchronization feature requires modern publishing on the
course instance. If the option is unavailable, verify the course's publishing
configuration and that your deployment includes PrairieLearn PR #15909.

Paste the CSV and click **Compare**. This is a student synchronization workflow,
not merely a label importer. Review invitations and enrollment removals, including
students omitted from your CSV, and deselect changes you do not want before applying.

**CSV label columns replace the student's entire label set.** Include every label
they should retain, including accommodations and labels maintained elsewhere.
The extension cannot discover existing memberships. Its output never intentionally
contains an empty label set. It accepts at most 5,000 students and 100 distinct labels.

PL format:

```csv
uid,label1,label2
alice@example.edu,Section A,SDC 1.5x
bob@example.edu,Section B,
```

Once assignments are in PL, configure each assessment's modern `accessControl`
label overrides in the Access page. No per-quiz GitHub workflow is required for
this extension. Roster changes still need another synchronization.

## Privacy and limitations

- No host permissions, content scripts, network calls, remote libraries, cookies,
  analytics or persistent browser storage. CSP blocks network requests.
- Parsed files exist only in the extension tab's memory. Closing/reloading the tab
  clears them; downloads and clipboard contents are not cleared automatically.
- This version converts and exports only. It does not inject into PL, assign labels,
  create labels, change enrollments or submit a synchronization automatically.
- CSV quoting supports BOM, CRLF/LF, quoted commas, escaped quotes and multiline
  cells. Malformed row widths and duplicate headers are rejected. Group labels
  must be single-line; one mapping value is one label.
- Clipboard availability depends on browser policy; CSV download is the fallback.

## Files

- `manifest.json`, `background.js`: installation and toolbar action.
- `index.html`, `styles.css`, `app.mjs`: local conversion workspace.
- `core.mjs`: CSV parsing, merging, validation and export.

Official references:

- [CSV label synchronization, PR #15909](https://github.com/PrairieLearn/PrairieLearn/pull/15909)
- [Student labels](https://docs.prairielearn.com/courseInstance/#student-labels)
- [Assessment access control](https://docs.prairielearn.com/assessment/accessControl/)
