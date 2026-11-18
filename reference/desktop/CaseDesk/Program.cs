namespace CaseDesk;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        var store = new CaseStore();
        using (var login = new LoginForm())
        {
            if (login.ShowDialog() != DialogResult.OK)
            {
                return;
            }
        }
        Application.Run(new MainForm(store));
    }
}
