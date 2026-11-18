using System.Windows;

namespace CaseDeskWpf;

public partial class CaseDetailWindow : Window
{
    private readonly Case _case;

    public CaseDetailWindow(Case item)
    {
        _case = item;
        InitializeComponent();
        Title = $"Case {item.CaseNo}";
        cboStatus.ItemsSource = CaseStore.Statuses;
        txtCaseNo.Text = item.CaseNo;
        txtCustomer.Text = item.Customer;
        txtSummary.Text = item.Summary;
        cboStatus.SelectedItem = item.Status;
        chkUrgent.IsChecked = item.Urgent;
        txtCustomer.Focus();
    }

    private void Save(object sender, RoutedEventArgs e)
    {
        var status = cboStatus.SelectedItem?.ToString() ?? "";
        var summary = txtSummary.Text.Trim();
        if (txtCustomer.Text.Trim().Length == 0)
        {
            MessageBox.Show(this, "Customer is required.", "Case", MessageBoxButton.OK,
                MessageBoxImage.Warning);
            return;
        }
        if ((status == "Resolved" || status == "Closed") && summary.Length < 10)
        {
            MessageBox.Show(this, "A resolved or closed case needs a summary of at least 10 characters.",
                "Case", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        _case.Customer = txtCustomer.Text.Trim();
        _case.Summary = summary;
        _case.Status = status;
        _case.Urgent = chkUrgent.IsChecked == true;
        DialogResult = true;
    }
}
