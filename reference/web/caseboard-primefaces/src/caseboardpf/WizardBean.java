package caseboardpf;

import java.io.Serializable;
import java.util.HashMap;
import java.util.Map;

import org.primefaces.PrimeFaces;
import org.primefaces.event.FlowEvent;
import org.primefaces.event.SelectEvent;

/** The new-case wizard (session scope): basics, owner (picked in a dialog-framework page), confirm. */
public class WizardBean implements Serializable {
    private static final long serialVersionUID = 1L;

    private Store store;
    private WorkBean work;
    private String title;
    private String priority = "Normal";
    private String owner;
    private String step = "basics";

    public void setStore(Store store) { this.store = store; }
    public void setWork(WorkBean work) { this.work = work; }

    public String onFlow(FlowEvent event) {
        step = event.getNewStep();
        return step;
    }

    /** The dialog framework: picker.xhtml opens in an iframe inside a PrimeFaces dialog. */
    public void pickOwner() {
        Map<String, Object> options = new HashMap<>();
        options.put("modal", true);
        options.put("width", 420);
        options.put("height", 300);
        options.put("contentWidth", "100%");
        options.put("contentHeight", "100%");
        options.put("headerElement", "ownerPickerHeader");
        PrimeFaces.current().dialog().openDynamic("picker", options, null);
    }

    public void onOwnerChosen(SelectEvent<Object> event) {
        owner = event.getObject() == null ? null : String.valueOf(event.getObject());
    }

    public void finish() {
        Item item = store.create(title, owner, priority);
        WorkBean.message("Case " + item.getNumber() + " created");
        title = null;
        owner = null;
        priority = "Normal";
        step = "basics";
        work.show("cases");
    }

    public String getStep() { return step; }
    public void setStep(String step) { this.step = step; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getPriority() { return priority; }
    public void setPriority(String priority) { this.priority = priority; }
    public String getOwner() { return owner; }
    public void setOwner(String owner) { this.owner = owner; }
}
