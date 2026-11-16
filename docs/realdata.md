# Real data in a lab: a masked slice of a replica

The lab normally runs on synthetic data or on an Excel workbook you fill in. When a bot only goes
wrong on real data, take a small slice of a **replica**: the records the bot touches, plus
everything they need. Mask it, then load it into the demo app's lab.

Nothing here writes to the replica. Unmasked rows never need to touch the disk.

## Before you start

- **A read-only login on a replica.** Never use production, and never a login that can write.
  For SQL Server that is `db_datareader` on the replica database. Twin Lab refuses anything but
  single read statements, but the login is the real protection.
- **The driver.** Postgres and SQLite work out of the box. For the others:
  - **SQL Server:** run `pip install -e ".[mssql]"`, and also install Microsoft's
    "ODBC Driver 18 for SQL Server".
  - **Oracle:** run `pip install -e ".[oracle]"`.
  - **MySQL:** run `pip install -e ".[mysql]"`.
- **The database address in an environment variable.** Commands take the variable's *name*,
  never the address. Messages show the address with its password replaced by `***`.

```powershell
$env:QTT_REPLICA = 'mssql+pyodbc://reader:...@replica-host/QTT?driver=ODBC+Driver+18+for+SQL+Server'
```

- **A masking key**, in the variable your masking manifest names. Keep it like a password: the
  same key always gives the same masked values.

## 1. See what is there

```bash
labctl realdata inventory QTT_REPLICA --out inventory.json
```

## 2. Say which records, and take them with what they need

Copy `lab/labctl/labctl/realdata/specs/subset.example.yaml` and change it. The **seeds** are the
records the bot works on: give either key values, or a `where:` condition with `:params`.
A `where:` must be one condition: no literal strings, no `;`, no comments. The closure adds every
parent row and a bounded number of child rows.

```bash
labctl realdata draft-manifest --url-env QTT_REPLICA --out mask.yaml
```

Read `mask.yaml`. It says how each column is masked: names from a dictionary, account numbers
with format-preserving encryption, emails with a keyed hash, dates shifted. The data owner reads
it and, when satisfied, sets `review: approved_by:` to their name. Masking refuses a manifest
nobody approved.

```bash
labctl realdata extract --url-env QTT_REPLICA --spec subset.yaml --manifest mask.yaml --out masked.json
```

With `--manifest`, rows are masked before anything is written. Twin Lab then checks the result:
no original value may survive in a masked column, and no two values may collapse into one. Next
to `masked.json` it writes `masked.json.mask-receipt.json`, which records what masked the file.

## 3. Load it into the lab

Real names rarely match the demo schema. Tables and columns match by name, ignoring case, a
schema prefix such as `dbo.` and underscores. Name the rest in a mapping file:

```yaml
# map.yaml
tables:  {QC_REVIEW_HDR: qc_reviews}
columns: {QC_REVIEW_HDR: {RVW_NBR: review_no}}
skip:    [AUDIT_ARCHIVE]
```

```bash
labctl realdata load qtt --in masked.json --map map.yaml --dry-run
```

The dry run shows every table it matched and every column it left out. Then run the same command
without `--dry-run`.

The lab agent checks every value against the demo schema (types, required fields, keys) and loads
everything or nothing. The loaded rows become the lab's baseline, so **Reset** returns to them.

`load` refuses a file without a valid mask receipt, and a file changed after masking. For a file
of synthetic rows, add `--not-real-data`.

## What to keep in mind

- `masked.json`, the inventory and the manifest stay on this machine. Do not commit them. A
  warning appears if an unmasked file would be tracked by Git.
- Masking keeps formats and joins: the same customer number masks the same way in every table.
  But a lab on masked data still is not production: values the bot types must exist in the
  masked set, so look them up there.
