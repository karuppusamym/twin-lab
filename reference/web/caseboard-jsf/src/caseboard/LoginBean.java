package caseboard;

import java.io.Serializable;

import javax.faces.application.FacesMessage;
import javax.faces.context.FacesContext;

/** Who is signed in (session scope). Any user name signs in with the synthetic password. */
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
        return "/cases.xhtml?faces-redirect=true";
    }

    public String signOut() {
        FacesContext.getCurrentInstance().getExternalContext().invalidateSession();
        return "/login.xhtml?faces-redirect=true";
    }

    /** View action of every protected page: sends a visitor who is not signed in to sign-in. */
    public String guard() {
        return signedIn ? null : "/login.xhtml?faces-redirect=true";
    }

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
    public boolean isSignedIn() { return signedIn; }
}
