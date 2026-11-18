namespace CaseDesk;

/// <summary>Detail dialog of one case: text boxes, a status combo box, an urgent checkbox,
/// Save and Cancel. Resolving or closing a case needs a summary of at least 10 characters.</summary>
public sealed class CaseDetailForm : Form
{
    private readonly Case _case;
    private readonly TextBox txtCaseNo = new() { Name = "txtCaseNo", Left = 110, Top = 16, Width = 240, ReadOnly = true, TabStop = false };
    private readonly TextBox txtCustomer = new() { Name = "txtCustomer", Left = 110, Top = 48, Width = 240, TabIndex = 1 };
    private readonly TextBox txtSummary = new() { Name = "txtSummary", Left = 110, Top = 80, Width = 240, Height = 60, Multiline = true, TabIndex = 3 };
    private readonly ComboBox cboStatus = new() { Name = "cboStatus", Left = 110, Top = 150, Width = 160, DropDownStyle = ComboBoxStyle.DropDownList, TabIndex = 5 };
    private readonly CheckBox chkUrgent = new() { Name = "chkUrgent", Text = "&Urgent", Left = 110, Top = 182, Width = 100, TabIndex = 6 };
    private readonly Button btnSave = new() { Name = "btnSave", Text = "&Save", Left = 180, Top = 220, Width = 80, TabIndex = 7 };
    private readonly Button btnCancel = new() { Name = "btnCancel", Text = "Cancel", Left = 270, Top = 220, Width = 80, TabIndex = 8, DialogResult = DialogResult.Cancel };

    public CaseDetailForm(Case item)
    {
        _case = item;
        Name = "CaseDetailForm";
        Text = $"Case {item.CaseNo}";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterParent;
        ClientSize = new Size(370, 260);
        var lblCaseNo = new Label { Name = "lblCaseNo", Text = "Case No", Left = 16, Top = 19, Width = 90 };
        var lblCustomer = new Label { Name = "lblCustomer", Text = "&Customer", Left = 16, Top = 51, Width = 90, TabIndex = 0 };
        var lblSummary = new Label { Name = "lblSummary", Text = "Su&mmary", Left = 16, Top = 83, Width = 90, TabIndex = 2 };
        var lblStatus = new Label { Name = "lblStatus", Text = "S&tatus", Left = 16, Top = 153, Width = 90, TabIndex = 4 };
        cboStatus.Items.AddRange(CaseStore.Statuses);
        Controls.AddRange(new Control[] { lblCaseNo, txtCaseNo, lblCustomer, txtCustomer, lblSummary, txtSummary, lblStatus, cboStatus, chkUrgent, btnSave, btnCancel });
        txtCaseNo.Text = item.CaseNo;
        txtCustomer.Text = item.Customer;
        txtSummary.Text = item.Summary;
        cboStatus.SelectedItem = item.Status;
        chkUrgent.Checked = item.Urgent;
        CancelButton = btnCancel;
        btnSave.Click += (_, _) => Save();
    }

    private void Save()
    {
        var status = cboStatus.SelectedItem?.ToString() ?? "";
        var summary = txtSummary.Text.Trim();
        if (txtCustomer.Text.Trim().Length == 0)
        {
            MessageBox.Show(this, "Customer is required.", "Case", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        if ((status == "Resolved" || status == "Closed") && summary.Length < 10)
        {
            MessageBox.Show(this, "A resolved or closed case needs a summary of at least 10 characters.",
                "Case", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        _case.Customer = txtCustomer.Text.Trim();
        _case.Summary = summary;
        _case.Status = status;
        _case.Urgent = chkUrgent.Checked;
        DialogResult = DialogResult.OK;
        Close();
    }
}
