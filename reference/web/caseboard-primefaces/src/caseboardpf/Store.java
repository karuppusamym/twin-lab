package caseboardpf;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** The cases, in memory (application scope): a fresh server starts from the same seventeen. */
public class Store {
    private static final String[] OWNERS = {"Okafor", "Lindqvist", "Marchetti", "Tanaka", "Abernathy"};
    private final List<Item> cases = new ArrayList<>();

    public Store() {
        add("Printer jam on floor three", "Okafor", "Open", "Normal");
        add("Badge reader offline", "Lindqvist", "Open", "High");
        add("Expense report rejected", "Okafor", "In progress", "Normal");
        add("New starter laptop", "Marchetti", "Open", "Low");
        add("Meeting room display flickers", "Lindqvist", "Closed", "Low");
        add("Payroll export missing rows", "Tanaka", "In progress", "High");
        add("Parking permit renewal", "Marchetti", "Open", "Low");
        add("Shared drive quota reached", "Okafor", "Open", "Normal");
        add("Phone line crackles", "Tanaka", "Closed", "Normal");
        add("Supplier address change", "Lindqvist", "Open", "Normal");
        add("Fire door alarm test", "Marchetti", "In progress", "High");
        add("Canteen card top-up fails", "Tanaka", "Open", "Normal");
        add("Travel report overdue", "Abernathy", "Open", "Normal");
        add("Desk move request", "Abernathy", "In progress", "Low");
        add("Monitor arm broken", "Okafor", "Open", "Low");
        add("Quarterly report template", "Lindqvist", "Open", "Normal");
        add("Locker key missing", "Marchetti", "Closed", "Low");
        cases.get(1).getNotes().add("Facilities informed");
        cases.get(5).getNotes().add("Finance checking the export");
        cases.get(5).getNotes().add("Second run planned");
    }

    private Item add(String title, String owner, String status, String priority) {
        Item item = new Item(2001 + cases.size(), title, owner, status, priority);
        cases.add(item);
        return item;
    }

    public synchronized Item create(String title, String owner, String priority) {
        return add(title, owner == null || owner.isEmpty() ? OWNERS[0] : owner, "Open",
                priority == null ? "Normal" : priority);
    }

    public List<String> getOwners() {
        return Arrays.asList(OWNERS);
    }

    public synchronized int getCount() {
        return cases.size();
    }

    public synchronized Item byNumber(String number) {
        for (Item c : cases) {
            if (c.getNumber().equals(number)) {
                return c;
            }
        }
        return null;
    }

    /** The cases whose title holds {@code title} (any case), sorted by one field. */
    public synchronized List<Item> query(String title, String sortField, boolean descending) {
        String q = title == null ? "" : title.trim().toLowerCase(Locale.ROOT);
        List<Item> out = new ArrayList<>();
        for (Item c : cases) {
            if (q.isEmpty() || c.getTitle().toLowerCase(Locale.ROOT).contains(q)) {
                out.add(c);
            }
        }
        Comparator<Item> by = null;
        if ("number".equals(sortField)) {
            by = Comparator.comparingInt(Item::getId);
        } else if ("owner".equals(sortField)) {
            by = Comparator.comparing(Item::getOwner).thenComparingInt(Item::getId);
        }
        if (by != null) {
            out.sort(descending ? by.reversed() : by);
        }
        return out;
    }

    /** Counts by a field: status, owner or priority, in the order first seen. */
    public synchronized List<Map.Entry<String, Integer>> countBy(String field) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (Item c : cases) {
            String key = "owner".equals(field) ? c.getOwner()
                    : "priority".equals(field) ? c.getPriority() : c.getStatus();
            counts.merge(key, 1, Integer::sum);
        }
        return Collections.unmodifiableList(new ArrayList<>(counts.entrySet()));
    }
}
