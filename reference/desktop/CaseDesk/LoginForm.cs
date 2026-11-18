namespace CaseDesk;

/// <summary>Sign-in dialog. Any user name with the synthetic password "demo" signs in.</summary>
public sealed class LoginForm : Form
{
    private readonly TextBox txtUser = new() { Name = "txtUser", Left = 110, Top = 20, Width = 180, TabIndex = 1 };
    private readonly TextBox txtPassword = new() { Name = "txtPassword", Left = 110, Top = 54, Width = 180, TabIndex = 3, UseSystemPasswordChar = true };
    private readonly Button btnSignIn = new() { Name = "btnSignIn", Text = "&Sign in", Left = 110, Top = 92, Width = 85, TabIndex = 4 };
    private readonly Button btnCancel = new() { Name = "btnCancel", Text = "Cancel", Left = 205, Top = 92, Width = 85, TabIndex = 5, DialogResult = DialogResult.Cancel };

    public LoginForm()
    {
        Name = "LoginForm";
        Text = "Sign in";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(320, 135);
        var lblUser = new Label { Name = "lblUser", Text = "&User name", Left = 16, Top = 23, Width = 90, TabIndex = 0 };
        var lblPassword = new Label { Name = "lblPassword", Text = "&Password", Left = 16, Top = 57, Width = 90, TabIndex = 2 };
        Controls.AddRange(new Control[] { lblUser, txtUser, lblPassword, txtPassword, btnSignIn, btnCancel });
        AcceptButton = btnSignIn;
        CancelButton = btnCancel;
        btnSignIn.Click += (_, _) => SignIn();
    }

    private void SignIn()
    {
        if (txtUser.Text.Trim().Length == 0 || txtPassword.Text != "demo")
        {
            MessageBox.Show(this, "Enter your user name and password.", "Sign in",
                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        DialogResult = DialogResult.OK;
        Close();
    }
}
