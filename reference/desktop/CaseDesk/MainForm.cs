namespace CaseDesk;

/// <summary>Main window: menu, search box, grid of cases, status bar.</summary>
public sealed class MainForm : Form
{
    private readonly CaseStore _store;
    private readonly TextBox txtSearch = new() { Name = "txtSearch", Left = 90, Top = 36, Width = 220, TabIndex = 1 };
    private readonly Button btnSearch = new() { Name = "btnSearch", Text = "&Search", Left = 320, Top = 34, Width = 80, TabIndex = 2 };
    private readonly Button btnOpen = new() { Name = "btnOpen", Text = "&Open", Left = 410, Top = 34, Width = 80, TabIndex = 3 };
    private readonly DataGridView dgvCases = new()
    {
        Name = "dgvCases", Left = 12, Top = 72, Width = 560, Height = 220, TabIndex = 4,
        ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false,
        SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
        RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
    };
    private readonly StatusStrip statusMain = new() { Name = "statusMain" };
    private readonly ToolStripStatusLabel lblCount = new() { Name = "lblCount", Text = "" };

    public MainForm(CaseStore store)
    {
        _store = store;
        Name = "MainForm";
        Text = "CaseDesk - Cases";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(590, 330);

        var menu = new MenuStrip { Name = "menuMain" };
        var mnuFile = new ToolStripMenuItem("&File") { Name = "mnuFile" };
        var mnuExit = new ToolStripMenuItem("E&xit") { Name = "mnuExit" };
        mnuExit.Click += (_, _) => Close();
        mnuFile.DropDownItems.Add(mnuExit);
        var mnuCase = new ToolStripMenuItem("&Case") { Name = "mnuCase" };
        var mnuOpenCase = new ToolStripMenuItem("&Open case") { Name = "mnuOpenCase", ShortcutKeys = Keys.Control | Keys.O };
        mnuOpenCase.Click += (_, _) => OpenSelected();
        var mnuRefresh = new ToolStripMenuItem("&Refresh") { Name = "mnuRefresh", ShortcutKeys = Keys.F5 };
        mnuRefresh.Click += (_, _) => RunSearch();
        mnuCase.DropDownItems.AddRange(new ToolStripItem[] { mnuOpenCase, mnuRefresh });
        var mnuHelp = new ToolStripMenuItem("&Help") { Name = "mnuHelp" };
        var mnuAbout = new ToolStripMenuItem("&About CaseDesk") { Name = "mnuAbout" };
        mnuAbout.Click += (_, _) => MessageBox.Show(this, "CaseDesk 1.2 (Twin Lab reference)", "About CaseDesk");
        mnuHelp.DropDownItems.Add(mnuAbout);
        menu.Items.AddRange(new ToolStripItem[] { mnuFile, mnuCase, mnuHelp });
        MainMenuStrip = menu;

        var lblSearch = new Label { Name = "lblSearch", Text = "Case or &name", Left = 12, Top = 39, Width = 76, TabIndex = 0 };
        dgvCases.Columns.Add(new DataGridViewTextBoxColumn { Name = "colCaseNo", HeaderText = "Case No", DataPropertyName = "CaseNo" });
        dgvCases.Columns.Add(new DataGridViewTextBoxColumn { Name = "colCustomer", HeaderText = "Customer", DataPropertyName = "Customer" });
        dgvCases.Columns.Add(new DataGridViewTextBoxColumn { Name = "colStatus", HeaderText = "Status", DataPropertyName = "Status" });
        dgvCases.Columns.Add(new DataGridViewTextBoxColumn { Name = "colOpened", HeaderText = "Opened", DataPropertyName = "Opened" });
        dgvCases.AutoGenerateColumns = false;
        dgvCases.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0) OpenSelected(); };

        statusMain.Items.Add(lblCount);
        Controls.AddRange(new Control[] { lblSearch, txtSearch, btnSearch, btnOpen, dgvCases, statusMain, menu });
        if (Environment.GetEnvironmentVariable("CASEDESK_VARIANT") == "drift")
        {
            // A deliberately wrong build, for Twin Lab's own tests of `labctl desktop verify`:
            // a renamed control (missing + extra) and a second control with the same id (ambiguous).
            btnOpen.Name = "btnView";
            Controls.Add(new TextBox { Name = "txtSearch", Left = 500, Top = 36, Width = 60 });
        }
        AcceptButton = btnSearch;
        btnSearch.Click += (_, _) => RunSearch();
        btnOpen.Click += (_, _) => OpenSelected();
        RunSearch();
    }

    private void RunSearch()
    {
        var found = _store.Search(txtSearch.Text);
        dgvCases.DataSource = found.Select(c => new { c.CaseNo, c.Customer, c.Status, Opened = c.Opened.ToString("yyyy-MM-dd") }).ToList();
        lblCount.Text = found.Count == 1 ? "1 case" : $"{found.Count} cases";
    }

    private void OpenSelected()
    {
        if (dgvCases.CurrentRow == null)
        {
            MessageBox.Show(this, "Select a case first.", "CaseDesk", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        var caseNo = dgvCases.CurrentRow.Cells[0].Value?.ToString() ?? "";
        var item = _store.Find(caseNo);
        if (item == null)
        {
            return;
        }
        using var detail = new CaseDetailForm(item);
        if (detail.ShowDialog(this) == DialogResult.OK)
        {
            RunSearch();
        }
    }
}
