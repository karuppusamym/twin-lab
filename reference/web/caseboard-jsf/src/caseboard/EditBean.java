package caseboard;

import java.io.Serializable;

/** The case being edited (view scope): a copy, written back to the store by Save. */
public class EditBean implements Serializable {
    private static final long serialVersionUID = 1L;

    private transient CaseStore store;
    private Integer id;
    private String number;
    private String title;
    private String owner;
    private String status;
    private String priority;
    private boolean urgent;
    private Integer hours;
    private String resolution;

    public void setStore(CaseStore store) { this.store = store; }

    /** View action: loads the case the address names; an unknown one goes back to the list. */
    public String load() {
        CaseItem c = id == null ? null : store.byId(id);
        if (c == null) {
            return "/cases.xhtml?faces-redirect=true";
        }
        number = c.getNumber();
        title = c.getTitle();
        owner = c.getOwner();
        status = c.getStatus();
        priority = c.getPriority();
        urgent = c.isUrgent();
        hours = c.getHours();
        resolution = c.getResolution();
        return null;
    }

    public String save() {
        CaseItem c = store.byId(id);
        if (c == null) {
            return "/cases.xhtml?faces-redirect=true";
        }
        c.setTitle(title.trim());
        c.setStatus(status);
        c.setPriority(priority);
        c.setUrgent(urgent);
        c.setHours(hours);
        c.setResolution(isClosed() && resolution != null ? resolution.trim() : "");
        return "/saved.xhtml?faces-redirect=true&id=" + id;
    }

    public CaseItem getSaved() { return id == null ? null : store.byId(id); }
    public boolean isClosed() { return "Closed".equals(status); }
    public String[] getStatuses() { return new String[] {"Open", "In progress", "Closed"}; }
    public String[] getPriorities() { return new String[] {"Low", "Normal", "High"}; }

    public Integer getId() { return id; }
    /** A postback carries no id in its address: the view parameter then offers null. */
    public void setId(Integer id) {
        if (id != null) {
            this.id = id;
        }
    }
    public String getNumber() { return number; }
    public String getOwner() { return owner; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getPriority() { return priority; }
    public void setPriority(String priority) { this.priority = priority; }
    public boolean isUrgent() { return urgent; }
    public void setUrgent(boolean urgent) { this.urgent = urgent; }
    public Integer getHours() { return hours; }
    public void setHours(Integer hours) { this.hours = hours; }
    public String getResolution() { return resolution; }
    public void setResolution(String resolution) { this.resolution = resolution; }
}
