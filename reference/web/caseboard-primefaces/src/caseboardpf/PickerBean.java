package caseboardpf;

import java.io.Serializable;

import org.primefaces.PrimeFaces;

/** The owner picker shown by the dialog framework (request scope): choosing closes the dialog. */
public class PickerBean implements Serializable {
    private static final long serialVersionUID = 1L;

    public void choose(String owner) {
        PrimeFaces.current().dialog().closeDynamic(owner);
    }
}
