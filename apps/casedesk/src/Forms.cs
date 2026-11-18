namespace CaseDeskDemo;

// Every Name (= UI Automation AutomationId), Text (= Name) and control type below is the one the
// brief lists for the window; controls sit directly on their form, as the brief's Path column says.

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        using var data = new Data();
        using (var login = new LoginForm())
        {
            if (login.ShowDialog() != DialogResult.OK) return;
        }
        Application.Run(new MainForm(data));
    }
}

/// <summary>Window "Sign in" (LoginForm). The test credential is synthetic: any user name,
/// password from CASEDESK_DEMO_PASSWORD (default "demo"); see BUILD-NOTES.md.</summary>
public sealed class LoginForm : Form
{
    private readonly TextBox txtUser = new() { Name = "txtUser", Left = 110, Top = 20, Width = 180, TabIndex = 1 };
    private readonly TextBox txtPassword = new() { Name = "txtPassword", Left = 110, Top = 54, Width = 180, TabIndex = 3, UseSystemPasswordChar = true };
    private readonly Button btnSignIn = new() { Name = "btnSignIn", Text = "Sign in", Left = 110, Top = 92, Width = 85, TabIndex = 4 };
    private readonly Button btnCancel = new() { Name = "btnCancel", Text = "Cancel", Left = 205, Top = 92, Width = 85, TabIndex = 5, DialogResult = DialogResult.Cancel };

    public LoginForm()
    {
        Name = "LoginForm";
        Text = "Sign in";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(320, 135);
        Controls.AddRange(new Control[]
        {
            new Label { Name = "lblUser", Text = "&User name", Left = 16, Top = 23, Width = 90, TabIndex = 0 }, txtUser,
            new Label { Name = "lblPassword", Text = "&Password", Left = 16, Top = 57, Width = 90, TabIndex = 2 }, txtPassword,
            btnSignIn, btnCancel,
        });
        AcceptButton = btnSignIn;
        CancelButton = btnCancel;
        btnSignIn.Click += (_, _) =>
        {
            var expected = Environment.GetEnvironmentVariable("CASEDESK_DEMO_PASSWORD") ?? "demo";
            if (txtUser.Text.Trim().Length == 0 || txtPassword.Text != expected)
            {
                // R1: message box "Sign in": Enter your user name and password.
                MessageBox.Show(this, "Enter your user name and password.", "Sign in", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            DialogResult = DialogResult.OK;
            Close();
        };
    }
}

/// <summary>Window "CaseDesk - Cases" (MainForm).</summary>
public sealed class MainForm : Form
{
    private readonly Data _data;
    private readonly TextBox txtSearch = new() { Name = "txtSearch", Left = 90, Top = 36, Width = 220, TabIndex = 1 };
    private readonly Button btnSearch = new() { Name = "btnSearch", Text = "Search", Left = 320, Top = 34, Width = 80, TabIndex = 2 };
    private readonly Button btnOpen = new() { Name = "btnOpen", Text = "Open", Left = 410, Top = 34, Width = 80, TabIndex = 3 };
    private readonly DataGridView dgvCases = new()
    {
        Name = "dgvCases", Left = 12, Top = 72, Width = 560, Height = 220, TabIndex = 4, ReadOnly = true,
        AllowUserToAddRows = false, AllowUserToDeleteRows = false, RowHeadersVisible = false,
        SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
        AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill, AutoGenerateColumns = false,
    };
    private readonly StatusStrip statusMain = new() { Name = "statusMain" };
    private readonly ToolStripStatusLabel lblCount = new() { Name = "lblCount" };

    public MainForm(Data data)
    {
        _data = data;
        Name = "MainForm";
        Text = "CaseDesk - Cases";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(590, 330);

        var menu = new MenuStrip { Name = "menuMain" };
        var file = new ToolStripMenuItem("&File");
        var exit = new ToolStripMenuItem("E&xit");
        exit.Click += (_, _) => Close();
        file.DropDownItems.Add(exit);
        var caseMenu = new ToolStripMenuItem("&Case");
        var openCase = new ToolStripMenuItem("&Open case") { ShortcutKeys = Keys.Control | Keys.O };
        openCase.Click += (_, _) => OpenSelected();
        var refresh = new ToolStripMenuItem("&Refresh") { ShortcutKeys = Keys.F5 };
        refresh.Click += (_, _) => RunSearch();
        caseMenu.DropDownItems.AddRange(new ToolStripItem[] { openCase, refresh });
        // Help: its items were never opened in the recording; the menu exists, empty (BUILD-NOTES).
        var help = new ToolStripMenuItem("&Help");
        menu.Items.AddRange(new ToolStripItem[] { file, caseMenu, help });
        MainMenuStrip = menu;

        foreach (var (name, header) in new[] { ("colCaseNo", "Case No"), ("colCustomer", "Customer"), ("colStatus", "Status"), ("colOpened", "Opened") })
            dgvCases.Columns.Add(new DataGridViewTextBoxColumn { Name = name, HeaderText = header });
        statusMain.Items.Add(lblCount);
        Controls.AddRange(new Control[]
        {
            new Label { Name = "lblSearch", Text = "Case or &name", Left = 12, Top = 39, Width = 76, TabIndex = 0 },
            txtSearch, btnSearch, btnOpen, dgvCases, statusMain, menu,
        });
        AcceptButton = btnSearch; // Enter in the search box searches (key Enter seen)
        btnSearch.Click += (_, _) => RunSearch();
        btnOpen.Click += (_, _) => OpenSelected();
        RunSearch();
    }

    private void RunSearch()
    {
        var q = "%" + txtSearch.Text.Trim() + "%";
        var rows = _data.Query("SELECT case_no, customer, status, opened FROM cases WHERE case_no LIKE ? OR customer LIKE ? ORDER BY case_no", q, q);
        dgvCases.Rows.Clear();
        foreach (var r in rows) dgvCases.Rows.Add(r[0], r[1], r[2], r[3]);
        lblCount.Text = rows.Count == 1 ? "1 case" : $"{rows.Count} cases";
    }

    private void OpenSelected()
    {
        if (dgvCases.CurrentRow == null)
        {
            MessageBox.Show(this, "Select a case first.", "CaseDesk", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        var caseNo = dgvCases.CurrentRow.Cells[0].Value?.ToString() ?? "";
        using var detail = new CaseDetailForm(_data, caseNo);
        if (detail.ShowDialog(this) == DialogResult.OK) RunSearch();
    }
}

/// <summary>Window "Case X-9999" (CaseDetailForm).</summary>
public sealed class CaseDetailForm : Form
{
    private static readonly string[] Statuses = { "Open", "In progress", "Resolved", "Closed" };
    private readonly Data _data;
    private readonly string _caseNo;
    private readonly TextBox txtCaseNo = new() { Name = "txtCaseNo", Left = 110, Top = 16, Width = 240, ReadOnly = true, TabStop = false };
    private readonly TextBox txtCustomer = new() { Name = "txtCustomer", Left = 110, Top = 48, Width = 240, TabIndex = 1 };
    private readonly TextBox txtSummary = new() { Name = "txtSummary", Left = 110, Top = 80, Width = 240, Height = 60, Multiline = true, TabIndex = 3 };
    private readonly ComboBox cboStatus = new() { Name = "cboStatus", Left = 110, Top = 150, Width = 160, DropDownStyle = ComboBoxStyle.DropDownList, TabIndex = 5 };
    private readonly CheckBox chkUrgent = new() { Name = "chkUrgent", Text = "Urgent", Left = 110, Top = 182, Width = 100, TabIndex = 6 };
    private readonly Button btnSave = new() { Name = "btnSave", Text = "Save", Left = 180, Top = 220, Width = 80, TabIndex = 7 };
    private readonly Button btnCancel = new() { Name = "btnCancel", Text = "Cancel", Left = 270, Top = 220, Width = 80, TabIndex = 8, DialogResult = DialogResult.Cancel };

    public CaseDetailForm(Data data, string caseNo)
    {
        _data = data;
        _caseNo = caseNo;
        Name = "CaseDetailForm";
        Text = $"Case {caseNo}";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterParent;
        ClientSize = new Size(370, 260);
        cboStatus.Items.AddRange(Statuses);
        Controls.AddRange(new Control[]
        {
            new Label { Name = "lblCaseNo", Text = "Case No", Left = 16, Top = 19, Width = 90 }, txtCaseNo,
            new Label { Name = "lblCustomer", Text = "&Customer", Left = 16, Top = 51, Width = 90, TabIndex = 0 }, txtCustomer,
            new Label { Name = "lblSummary", Text = "Su&mmary", Left = 16, Top = 83, Width = 90, TabIndex = 2 }, txtSummary,
            new Label { Name = "lblStatus", Text = "Status", Left = 16, Top = 153, Width = 90, TabIndex = 4 }, cboStatus,
            chkUrgent, btnSave, btnCancel,
        });
        var row = data.Query("SELECT customer, summary, status, urgent FROM cases WHERE case_no = ?", caseNo).FirstOrDefault();
        txtCaseNo.Text = caseNo;
        if (row != null)
        {
            txtCustomer.Text = row[0];
            txtSummary.Text = row[1];
            cboStatus.SelectedItem = row[2];
            chkUrgent.Checked = row[3] == "1";
        }
        CancelButton = btnCancel;
        btnSave.Click += (_, _) => Save();
    }

    private void Save()
    {
        var status = cboStatus.SelectedItem?.ToString() ?? "";
        var summary = txtSummary.Text.Trim();
        if ((status == "Resolved" || status == "Closed") && summary.Length < 10)
        {
            // R2/R3: message box "Case" after Save.
            MessageBox.Show(this, "A resolved or closed case needs a summary of at least 10 characters.", "Case",
                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        _data.Query("UPDATE cases SET customer = ?, summary = ?, status = ?, urgent = ? WHERE case_no = ?",
            txtCustomer.Text.Trim(), summary, status, chkUrgent.Checked ? "1" : "0", _caseNo);
        DialogResult = DialogResult.OK;
        Close();
    }
}
