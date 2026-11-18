using System.Windows;

namespace CaseDeskWpf;

public partial class LoginWindow : Window
{
    public LoginWindow()
    {
        InitializeComponent();
    }

    private void SignIn(object sender, RoutedEventArgs e)
    {
        if (txtUser.Text.Trim().Length == 0 || txtPassword.Password != "demo")
        {
            MessageBox.Show(this, "Enter your user name and password.", "Sign in",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        DialogResult = true;
    }
}
