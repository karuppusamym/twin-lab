package caseboard;

import java.io.Serializable;

/** One synthetic case. */
public class CaseItem implements Serializable {
    private static final long serialVersionUID = 1L;

    private final int id;
    private String title;
    private String owner;
    private String status;
    private String priority;
    private boolean urgent;
    private Integer hours;
    private String resolution = "";

    public CaseItem(int id, String title, String owner, String status, String priority,
            boolean urgent, int hours) {
        this.id = id;
        this.title = title;
        this.owner = owner;
        this.status = status;
        this.priority = priority;
        this.urgent = urgent;
        this.hours = hours;
    }

    public int getId() { return id; }
    public String getNumber() { return "C-" + id; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getOwner() { return owner; }
    public void setOwner(String owner) { this.owner = owner; }
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
