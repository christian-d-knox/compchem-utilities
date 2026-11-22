# Recipe: Excel Workbooks for Documents (Formulas and Recalculation)

Every number that goes into a document the human keeps (reaction energy profiles, Delta G, BDE, and population tables,
SI values, and figure labels) should be an Excel formula in a project workbook, never a value computed by a script,
`python -c`, or mental arithmetic. That lets the human click any number and trace it back to a formula, the raw
GoodVibes values, and the dated master file. Quick scratch checks that end up in no document may use a calculator
tool; state which one was used.

## 1. The Master Copy

1. Run GoodVibes on the cluster through CompUtils to write `GoodVibes.xlsx` (see `{cu} -agent delta-g-table`):

       {cu} -gv '*.out' --spc SP --json

2. Copy `GoodVibes.xlsx` down to the human's local project folder under a dated name:

       scp {host}:FOLDER/GoodVibes.xlsx LOCAL_FOLDER/GoodVibes_20261119-1430.xlsx

3. Never edit or overwrite a master file. Each new GoodVibes run gets a new dated master copy.

## 2. The Project Workbook

Maintain one workbook per project (agreed with the human, e.g. `alkene-hydroarylation_calculations.xlsx`) in the same
local folder:

| Tab | What it holds |
|---|---|
| `GoodVibes Raw` | An exact cell-for-cell copy of one master's sheet. Nothing added, removed, sorted, or reformatted. |
| `Sources` | Which master the raw tab came from, the run's temperature and concentration (from the GoodVibes report), and the date. |
| One per document | `REP Pathway A`, `Delta G Table`, `BDEs`, ... Formulas only, plus labels and typed constants. |

- **Look values up by name, never by fixed cell coordinates.** Every formula finds its value by structure name and
  column header so a re-run that adds or reorders rows cannot silently break it:

      =INDEX('GoodVibes Raw'!A:Z, MATCH("TS1", 'GoodVibes Raw'!A:A, 0), MATCH("qh-G(T)_SPC", 'GoodVibes Raw'!1:1, 0))

- **Constants:** Put conversion factors (`627.5095` kcal/mol per Hartree) in labelled cells and reference them in
  formulas.
- **Multi-species states & mass balance:** For a stationary point with multiple species (e.g., complex + free substrate),
  sum the species lookups in one formula. Place a mass balance check beside it: type each species' element counts
  from `formula` in `{cu} -info '*.out' --json` and sum/compare both sides of every step using Excel formulas. If a
  step does not balance, stop and tell the human.
- **Default free-energy column:** Use `qh-G(T)_SPC` unless the human specifies otherwise, and name the column in the tab.

### Building and Updating with `openpyxl`

Build the workbook with `openpyxl` using CompUtils' local Python on the user's computer (recorded in your `computils`
skill's This Computer section). If This Computer says local CompUtils was declined, tell the human and give them the
formulas to enter manually instead.

Once per session before creating or updating the first workbook, run local CompUtils' `-env --json` check using the
local `cu` path from This Computer (`CU_LOCAL -env --json`). It verifies the local environment (including `openpyxl`)
and surfaces daily local update notices when present.

Save any helper script beside your notes, not in the project folder. A script may copy cells and write formulas and
labels, and must never write pre-computed numerical results:

```python
import openpyxl

master = openpyxl.load_workbook(r"LOCAL_FOLDER\GoodVibes_20261119-1430.xlsx").active
book = openpyxl.Workbook()
raw = book.active
raw.title = "GoodVibes Raw"
for row in master.iter_rows():  # exact cell-for-cell copy
    for cell in row:
        raw.cell(row=cell.row, column=cell.column, value=cell.value)

sources = book.create_sheet("Sources")
sources.append(["Raw tab copied from", "GoodVibes_20261119-1430.xlsx"])

rep = book.create_sheet("REP Pathway A")
rep.append(["Hartree to kcal/mol", 627.5095])
rep.append(["Point", "qh-G(T)_SPC (Hartree)", "Delta G (kcal/mol)"])
for point in ("1", "TS1", "2"):
    r = rep.max_row + 1
    rep.append([
        point,
        f"=INDEX('GoodVibes Raw'!A:Z,MATCH(\"{point}\",'GoodVibes Raw'!A:A,0),"
        f"MATCH(\"qh-G(T)_SPC\",'GoodVibes Raw'!1:1,0))",
        f"=(B{r}-B3)*B1",
    ])
book.save(r"LOCAL_FOLDER\alkene-hydroarylation_calculations.xlsx")
```

- **When a new master arrives:** Replace `GoodVibes Raw` contents with an exact copy of the new master and update
  `Sources`; existing formulas pick up the updated values.
- **If the human edited the workbook:** `openpyxl` drops Excel charts, images, and custom drawings it did not create.
  Save your updated file as a new version (e.g. `..._v2.xlsx`) and tell the human rather than overwriting theirs.

## 3. Let Excel Calculate, Then Read

`openpyxl` writes formulas without evaluating cached values. Have Microsoft Excel itself calculate and save the
workbook before reading values back:

**Windows (PowerShell):**

```powershell
$book = 'LOCAL_FOLDER\alkene-hydroarylation_calculations.xlsx'
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
try {
    $wb = $excel.Workbooks.Open($book)
    if ($wb.ReadOnly) { throw 'Workbook is open elsewhere: ask the human to close it in Excel and retry.' }
    $excel.CalculateFull()
    $wb.Save()
    $wb.Close($false)
} finally {
    $excel.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
}
```

**macOS (zsh, Excel for Mac via AppleScript):**

```zsh
book="/Users/NAME/PROJECT/alkene-hydroarylation_calculations.xlsx"
osascript \
  -e 'on run argv' \
  -e 'tell application "Microsoft Excel"' \
  -e 'set wb to open workbook workbook file name (POSIX file (item 1 of argv) as text)' \
  -e 'if read only of wb then error "Workbook is open elsewhere: ask the human to close it in Excel."' \
  -e 'calculate full' \
  -e 'save wb' \
  -e 'close wb saving no' \
  -e 'end tell' \
  -e 'end run' "$book"
```

- **First run on macOS:** Tell the human beforehand that macOS will ask twice (one-time permissions): Automation control
  of Microsoft Excel (Privacy & Security) and Grant File Access for the project folder. If declined or unsuccessful,
  ask the human to open and save the workbook in Excel.
- **If Excel is unavailable** (Windows "Class not registered" or macOS cannot find Microsoft Excel): ask the human to
  open and save the workbook in Excel. Do not substitute Apple Numbers or LibreOffice for final Excel values.
- **Read back calculated values** with `openpyxl(..., data_only=True)` using CompUtils' local Python (`PY_LOCAL`):

      PY_LOCAL -c "import openpyxl; ws = openpyxl.load_workbook('LOCAL_FOLDER/alkene-hydroarylation_calculations.xlsx', data_only=True)['REP Pathway A']; print([[c.value for c in row] for row in ws.iter_rows(min_row=3)])"

- **`None` values** mean Excel has not calculated and saved the workbook yet. **`#N/A` or formula errors** mean a lookup
  name or header does not match: fix the formula, never hardcode a value.

## 4. Reporting

Cite the workbook filename, tab, lookup column (e.g. `qh-G(T)_SPC`), and the temperature/concentration recorded in
`Sources` whenever reporting values.
