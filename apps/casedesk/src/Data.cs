using System.Runtime.InteropServices;

namespace CaseDeskDemo;

/// <summary>The demo's data: SQLite through Windows' own engine (winsqlite3.dll, part of
/// Windows 10 and later), so the build needs no package download. Tables come from
/// db/schema.sql (copied next to the executable); synthetic rows are seeded on first start.</summary>
public sealed class Data : IDisposable
{
    private const string Lib = "winsqlite3.dll";
    [DllImport(Lib, CharSet = CharSet.Ansi)] private static extern int sqlite3_open(string file, out IntPtr db);
    [DllImport(Lib)] private static extern int sqlite3_close(IntPtr db);
    [DllImport(Lib, CharSet = CharSet.Ansi)] private static extern int sqlite3_exec(IntPtr db, string sql, IntPtr cb, IntPtr arg, out IntPtr err);
    [DllImport(Lib)] private static extern int sqlite3_prepare16_v2(IntPtr db, [MarshalAs(UnmanagedType.LPWStr)] string sql, int n, out IntPtr stmt, IntPtr tail);
    [DllImport(Lib)] private static extern int sqlite3_bind_text16(IntPtr stmt, int i, [MarshalAs(UnmanagedType.LPWStr)] string value, int n, IntPtr destructor);
    [DllImport(Lib)] private static extern int sqlite3_step(IntPtr stmt);
    [DllImport(Lib)] private static extern IntPtr sqlite3_column_text16(IntPtr stmt, int col);
    [DllImport(Lib)] private static extern int sqlite3_column_count(IntPtr stmt);
    [DllImport(Lib)] private static extern int sqlite3_finalize(IntPtr stmt);
    private static readonly IntPtr Transient = new(-1);

    private readonly IntPtr _db;

    public Data()
    {
        var url = Environment.GetEnvironmentVariable("DATABASE_URL");
        var file = url != null && url.StartsWith("sqlite:///") ? url["sqlite:///".Length..]
            : Path.Combine(AppContext.BaseDirectory, "casedesk.db");
        var fresh = !File.Exists(file);
        if (sqlite3_open(file, out _db) != 0) throw new InvalidOperationException($"cannot open {file}");
        if (fresh || Query("SELECT name FROM sqlite_master WHERE type='table' AND name='cases'").Count == 0)
        {
            Exec(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "schema.sql")));
            Seed();
        }
    }

    private void Exec(string sql)
    {
        if (sqlite3_exec(_db, sql, IntPtr.Zero, IntPtr.Zero, out _) != 0) throw new InvalidOperationException("SQL failed");
    }

    public List<string[]> Query(string sql, params string[] args)
    {
        var rows = new List<string[]>();
        if (sqlite3_prepare16_v2(_db, sql, -1, out var stmt, IntPtr.Zero) != 0) throw new InvalidOperationException("SQL failed");
        try
        {
            for (var i = 0; i < args.Length; i++) sqlite3_bind_text16(stmt, i + 1, args[i], -1, Transient);
            var n = sqlite3_column_count(stmt);
            while (sqlite3_step(stmt) == 100)
            {
                var row = new string[n];
                for (var c = 0; c < n; c++) row[c] = Marshal.PtrToStringUni(sqlite3_column_text16(stmt, c)) ?? "";
                rows.Add(row);
            }
        }
        finally { sqlite3_finalize(stmt); }
        return rows;
    }

    private void Seed()
    {
        // Synthetic rows of the recorded shapes (case number X-9999, dates 9999-99-99).
        string[][] rows =
        {
            new[] { "C-1001", "Alex Synthetic", "Card not received", "Open", "2026-10-01", "0" },
            new[] { "C-1002", "Bea Fictional", "Address change", "In progress", "2026-10-03", "0" },
            new[] { "C-1003", "Cy Placeholder", "Fee refund request", "Open", "2026-10-07", "1" },
            new[] { "C-2001", "Dee Invented", "Statement copy", "Closed", "2026-09-21", "0" },
        };
        foreach (var r in rows)
            Query("INSERT INTO cases (case_no, customer, summary, status, opened, urgent) VALUES (?, ?, ?, ?, ?, ?)",
                r[0], r[1], r[2], r[3], r[4], r[5]);
    }

    public void Dispose() => sqlite3_close(_db);
}
