package caseboardpf;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

/** One case. Synthetic data only. */
public class Item implements Serializable {
    private static final long serialVersionUID = 1L;

    private final int id;
    private String title;
    private String owner;
    private String status;
    private String priority;
    private final List<String> notes = new ArrayList<>();
    private final List<String> history = new ArrayList<>();

    public Item(int id, String title, String owner, String status, String priority) {
        this.id = id;
        this.title = title;
        this.owner = owner;
        this.status = status;
        this.priority = priority;
        history.add("Opened as " + status);
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
    public List<String> getNotes() { return notes; }
    public List<String> getHistory() { return history; }
    public int getNoteCount() { return notes.size(); }
}
