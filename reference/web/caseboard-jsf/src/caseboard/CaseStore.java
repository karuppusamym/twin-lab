package caseboard;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** The cases, in memory (application scope): a fresh server starts from the same twelve. */
public class CaseStore {
    private final List<CaseItem> cases = new ArrayList<>();

    public CaseStore() {
        add("Printer jam on floor 3", "Okafor", "Open", "Normal", false, 2);
        add("Badge reader offline", "Lindqvist", "Open", "High", true, 4);
        add("Expense report rejected", "Okafor", "In progress", "Normal", false, 1);
        add("New starter laptop", "Marchetti", "Open", "Low", false, 3);
        add("Meeting room display flickers", "Lindqvist", "Closed", "Low", false, 2);
        add("Payroll export missing rows", "Tanaka", "In progress", "High", true, 8);
        add("Parking permit renewal", "Marchetti", "Open", "Low", false, 1);
        add("Shared drive quota reached", "Okafor", "Open", "Normal", false, 5);
        add("Phone line crackles", "Tanaka", "Closed", "Normal", false, 2);
        add("Supplier address change", "Lindqvist", "Open", "Normal", false, 1);
        add("Fire door alarm test", "Marchetti", "In progress", "High", false, 6);
        add("Canteen card top-up fails", "Tanaka", "Open", "Normal", false, 3);
    }

    private void add(String title, String owner, String status, String priority, boolean urgent,
            int hours) {
        cases.add(new CaseItem(1001 + cases.size(), title, owner, status, priority, urgent, hours));
    }

    public synchronized List<CaseItem> find(String query) {
        String q = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        List<CaseItem> out = new ArrayList<>();
        for (CaseItem c : cases) {
            if (q.isEmpty() || c.getTitle().toLowerCase(Locale.ROOT).contains(q)
                    || c.getOwner().toLowerCase(Locale.ROOT).contains(q)
                    || c.getNumber().toLowerCase(Locale.ROOT).contains(q)) {
                out.add(c);
            }
        }
        return out;
    }

    public synchronized CaseItem byId(int id) {
        for (CaseItem c : cases) {
            if (c.getId() == id) {
                return c;
            }
        }
        return null;
    }
}
