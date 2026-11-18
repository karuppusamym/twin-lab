using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;

namespace CaseDeskWpf;

public partial class MainWindow : Window
{
    /// <summary>One grid row: what the grid shows of a case.</summary>
    public sealed record Row(string CaseNo, string Customer, string Status, string Opened);

    private readonly CaseStore _store;

    public MainWindow(CaseStore store)
    {
        _store = store;
        InitializeComponent();
        InputBindings.Add(new KeyBinding(new Relay(OpenSelected), Key.O, ModifierKeys.Control));
        InputBindings.Add(new KeyBinding(new Relay(RunSearch), Key.F5, ModifierKeys.None));
        if (Environment.GetEnvironmentVariable("CASEDESK_VARIANT") == "drift")
        {
            // A deliberately wrong build, for Twin Lab's own tests of `labctl desktop verify`:
            // a renamed control (missing + extra) and a second control with the same id (ambiguous).
            AutomationProperties.SetAutomationId(btnOpen, "btnView");
            var twin = new TextBox { Width = 60, Height = 23, Margin = new Thickness(10, 0, 0, 0) };
            AutomationProperties.SetAutomationId(twin, "txtSearch");
            pnlSearch.Children.Add(twin);
        }
        RunSearch();
        txtSearch.Focus();
    }

    private void Search(object sender, RoutedEventArgs e) => RunSearch();

    private void Refresh(object sender, RoutedEventArgs e) => RunSearch();

    private void OpenCase(object sender, RoutedEventArgs e) => OpenSelected();

    private void Exit(object sender, RoutedEventArgs e) => Close();

    private void About(object sender, RoutedEventArgs e) =>
        MessageBox.Show(this, "CaseDesk 1.2 (Twin Lab reference)", "About CaseDesk");

    private void RowDoubleClick(object sender, MouseButtonEventArgs e)
    {
        if (e.OriginalSource is DependencyObject source
            && ItemsControl.ContainerFromElement(dgvCases, source) is DataGridRow)
        {
            OpenSelected();
        }
    }

    private void RunSearch()
    {
        var found = _store.Search(txtSearch.Text);
        dgvCases.ItemsSource = found
            .Select(c => new Row(c.CaseNo, c.Customer, c.Status, c.Opened.ToString("yyyy-MM-dd")))
            .ToList();
        if (found.Count > 0)
        {
            dgvCases.SelectedIndex = 0;  // as WinForms: the first row is the current row
        }
        lblCount.Text = found.Count == 1 ? "1 case" : $"{found.Count} cases";
    }

    private void OpenSelected()
    {
        if (dgvCases.SelectedItem is not Row row)
        {
            MessageBox.Show(this, "Select a case first.", "CaseDesk", MessageBoxButton.OK,
                MessageBoxImage.Information);
            return;
        }
        var item = _store.Find(row.CaseNo);
        if (item == null)
        {
            return;
        }
        var detail = new CaseDetailWindow(item) { Owner = this };
        if (detail.ShowDialog() == true)
        {
            RunSearch();
        }
    }

    private sealed class Relay : ICommand
    {
        private readonly Action _run;

        public Relay(Action run) => _run = run;

        public event EventHandler? CanExecuteChanged { add { } remove { } }

        public bool CanExecute(object? parameter) => true;

        public void Execute(object? parameter) => _run();
    }
}
