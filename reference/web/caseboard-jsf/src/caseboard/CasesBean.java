package caseboard;

import java.io.Serializable;
import java.util.List;

/** The list: what was searched for and which page shows (session scope). */
public class CasesBean implements Serializable {
    private static final long serialVersionUID = 1L;
    private static final int PAGE_SIZE = 5;

    private transient CaseStore store;
    private String query = "";
    private int page;

    public void setStore(CaseStore store) { this.store = store; }

    private List<CaseItem> found() {
        return store.find(query);
    }

    public List<CaseItem> getRows() {
        List<CaseItem> all = found();
        int from = Math.min(page * PAGE_SIZE, all.size());
        return all.subList(from, Math.min(from + PAGE_SIZE, all.size()));
    }

    public String search() {
        page = 0;
        return null;
    }

    public String next() {
        if (isHasNext()) {
            page++;
        }
        return null;
    }

    public String previous() {
        if (page > 0) {
            page--;
        }
        return null;
    }

    public String open(CaseItem item) {
        return "/edit.xhtml?faces-redirect=true&id=" + item.getId();
    }

    public int getTotal() { return found().size(); }
    public int getPageNumber() { return page + 1; }
    public int getPageCount() { return Math.max(1, (getTotal() + PAGE_SIZE - 1) / PAGE_SIZE); }
    public boolean isHasNext() { return page + 1 < getPageCount(); }
    public boolean isHasPrevious() { return page > 0; }
    public String getQuery() { return query; }
    public void setQuery(String query) { this.query = query == null ? "" : query; }
}
