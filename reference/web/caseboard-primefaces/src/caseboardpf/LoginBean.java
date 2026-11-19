package caseboardpf;

import java.io.Serializable;

import javax.faces.application.FacesMessage;
import javax.faces.context.FacesContext;

/**
 * Who is signed in (session scope). Any user name signs in with the synthetic password.
 * Signing in FORWARDS to the workspace (no redirect): the address bar keeps showing
 * /login.xhtml, as in many office JSF applications.
 */
public class LoginBean implements Serializable {
    private static final long serialVersionUID = 1L;
    private static final String PASSWORD = "demo";

    private String username;
    private String password;
    private boolean signedIn;

    public String signIn() {
        if (!PASSWORD.equals(password)) {
            signedIn = false;
            FacesContext.getCurrentInstance().addMessage(null, new FacesMessage(
                    FacesMessage.SEVERITY_ERROR, "Wrong user name or password", null));
            return null;
        }
        signedIn = true;
        password = null;
        return "workspace"; // a forward: same request, no redirect
    }

    public String signOut() {
        FacesContext.getCurrentInstance().getExternalContext().invalidateSession();
        return "/login.xhtml?faces-redirect=true";
    }

    /** View action of every protected page: a visitor who is not signed in goes to sign-in. */
    public String guard() {
        return signedIn ? null : "/login.xhtml?faces-redirect=true";
    }

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
    public boolean isSignedIn() { return signedIn; }
}
