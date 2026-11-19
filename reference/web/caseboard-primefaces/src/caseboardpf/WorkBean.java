package caseboardpf;

import java.io.Serializable;
import java.util.List;
import java.util.Map;

import javax.faces.application.FacesMessage;
import javax.faces.context.FacesContext;

import org.primefaces.event.SelectEvent;
import org.primefaces.model.FilterMeta;
import org.primefaces.model.LazyDataModel;
import org.primefaces.model.SortMeta;
import org.primefaces.model.SortOrder;

/**
 * The workspace (session scope): which sub-page the one address shows (a dynamic ui:include),
 * the lazy case table with its selection, the quick-note dialog and the case shown in detail.
 */
public class WorkBean implements Serializable {
    private static final long serialVersionUID = 1L;

    private Store store;
    private String page = "cases";
    private String selectedNumber;
    private String detailNumber;
    private String noteText;
    private LazyDataModel<Item> model;

    public void setStore(Store store) { this.store = store; }

    // --- which sub-page -----------------------------------------------------------------------
    public String getPage() { return page; }
    public String getInclude() { return "/WEB-INF/sub/" + page + ".xhtml"; }
    public void show(String which) { this.page = which; }

    // --- the lazy table -------------------------------------------------------------------------
    public LazyDataModel<Item> getModel() {
        if (model == null) {
            model = new CaseModel(store);
        }
        return model;
    }

    /** PrimeFaces 10's lazy model: one page of rows, sorted and filtered by the server. */
    public static final class CaseModel extends LazyDataModel<Item> {
        private static final long serialVersionUID = 1L;
        private final Store store;

        CaseModel(Store store) { this.store = store; }

        @Override
        public List<Item> load(int first, int pageSize, Map<String, SortMeta> sortBy,
                Map<String, FilterMeta> filterBy) {
            String title = null;
            FilterMeta f = filterBy == null ? null : filterBy.get("title");
            if (f != null && f.getFilterValue() != null) {
                title = String.valueOf(f.getFilterValue());
            }
            String field = null;
            boolean descending = false;
            if (sortBy != null) {
                for (SortMeta s : sortBy.values()) {
                    if (s.getOrder() != SortOrder.UNSORTED) {
                        field = s.getField();
                        descending = s.getOrder() == SortOrder.DESCENDING;
                        break;
                    }
                }
            }
            List<Item> all = store.query(title, field, descending);
            setRowCount(all.size());
            int to = Math.min(first + pageSize, all.size());
            return first >= to ? all.subList(0, 0) : all.subList(first, to);
        }

        @Override
        public Item getRowData(String rowKey) { return store.byNumber(rowKey); }

        @Override
        public String getRowKey(Item item) { return item.getNumber(); }
    }

    public Item getSelected() { return selectedNumber == null ? null : store.byNumber(selectedNumber); }
    public void setSelected(Item item) { this.selectedNumber = item == null ? null : item.getNumber(); }
    public void onRowSelect(SelectEvent<Item> event) { setSelected(event.getObject()); }
    public boolean isNothingSelected() { return selectedNumber == null; }
    public int getCount() { return store.getCount(); }

    // --- actions on the selected case -------------------------------------------------------------
    public void openDetail() {
        detailNumber = selectedNumber;
        page = "detail";
    }

    /** A full postback that FORWARDS to case.xhtml: the address bar keeps the previous page. */
    public String openPage() {
        detailNumber = selectedNumber;
        return "case";
    }

    public String backToWorkspace() {
        page = "cases";
        return "workspace";
    }

    public void closeSelected() {
        Item item = getSelected();
        if (item != null) {
            item.setStatus("Closed");
            item.getHistory().add("Closed from the list");
            message("Case closed");
        }
    }

    public String getNoteText() { return noteText; }
    public void setNoteText(String noteText) { this.noteText = noteText; }

    public void saveNote() {
        Item item = getSelected();
        if (item != null && noteText != null && !noteText.trim().isEmpty()) {
            item.getNotes().add(noteText.trim());
            item.getHistory().add("Note added");
            message("Note saved");
        }
        noteText = null;
    }

    public Item getDetail() { return detailNumber == null ? null : store.byNumber(detailNumber); }

    static void message(String text) {
        FacesContext.getCurrentInstance().addMessage(null,
                new FacesMessage(FacesMessage.SEVERITY_INFO, text, null));
    }
}
