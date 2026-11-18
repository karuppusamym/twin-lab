namespace CaseDesk;

/// <summary>One case. Synthetic data only.</summary>
public sealed class Case
{
    public string CaseNo { get; set; } = "";
    public string Customer { get; set; } = "";
    public string Summary { get; set; } = "";
    public string Status { get; set; } = "Open";
    public bool Urgent { get; set; }
    public DateTime Opened { get; set; }
}

/// <summary>The cases, in memory. Every run starts from the same synthetic rows.</summary>
public sealed class CaseStore
{
    public static readonly string[] Statuses = { "Open", "In progress", "Resolved", "Closed" };

    private readonly List<Case> _cases = new()
    {
        new Case { CaseNo = "C-1001", Customer = "Ada Example", Summary = "Card not received", Status = "Open", Opened = new DateTime(2026, 10, 1) },
        new Case { CaseNo = "C-1002", Customer = "Ben Sample", Summary = "Address change", Status = "In progress", Opened = new DateTime(2026, 10, 3) },
        new Case { CaseNo = "C-1003", Customer = "Cleo Demo", Summary = "Fee refund request", Status = "Open", Urgent = true, Opened = new DateTime(2026, 10, 7) },
        new Case { CaseNo = "C-2001", Customer = "Dan Synthetic", Summary = "Statement copy", Status = "Closed", Opened = new DateTime(2026, 9, 21) },
    };

    public IReadOnlyList<Case> Search(string text)
    {
        text = (text ?? "").Trim();
        return _cases
            .Where(c => text.Length == 0
                        || c.CaseNo.Contains(text, StringComparison.OrdinalIgnoreCase)
                        || c.Customer.Contains(text, StringComparison.OrdinalIgnoreCase))
            .ToList();
    }

    public Case? Find(string caseNo) => _cases.FirstOrDefault(c => c.CaseNo == caseNo);
}
