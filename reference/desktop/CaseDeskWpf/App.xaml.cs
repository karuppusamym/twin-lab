using System.Windows;

namespace CaseDeskWpf;

public partial class App : Application
{
    private void OnStartup(object sender, StartupEventArgs e)
    {
        var store = new CaseStore();
        if (new LoginWindow().ShowDialog() != true)
        {
            Shutdown();
            return;
        }
        var main = new MainWindow(store);
        MainWindow = main;
        ShutdownMode = ShutdownMode.OnMainWindowClose;
        main.Show();
    }
}
