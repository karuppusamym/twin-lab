package caseboard;

import java.io.File;
import java.io.IOException;
import java.net.InetSocketAddress;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.eclipse.jetty.server.Server;
import org.eclipse.jetty.servlet.ServletHolder;
import org.eclipse.jetty.webapp.WebAppContext;

/** Starts Caseboard on embedded Jetty: {@code java caseboard.Main --port 8137 --webapp webapp}. */
public final class Main {
    private Main() { }

    /** The bare address goes to sign-in. */
    public static final class Home extends HttpServlet {
        private static final long serialVersionUID = 1L;

        @Override
        protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
            resp.sendRedirect("/login.xhtml");
        }
    }

    public static void main(String[] args) throws Exception {
        int port = 8137;
        String webapp = "webapp";
        for (int i = 0; i + 1 < args.length; i += 2) {
            if ("--port".equals(args[i])) {
                port = Integer.parseInt(args[i + 1]);
            } else if ("--webapp".equals(args[i])) {
                webapp = args[i + 1];
            }
        }
        Server server = new Server(new InetSocketAddress("127.0.0.1", port));
        WebAppContext context = new WebAppContext();
        context.setContextPath("/");
        context.setResourceBase(new File(webapp).getAbsolutePath());
        context.setParentLoaderPriority(true); // the application and JSF are on the class path
        context.addServlet(new ServletHolder(new Home()), "");
        server.setHandler(context);
        server.start();
        if (context.getUnavailableException() != null) {
            server.stop();
            throw new IllegalStateException(context.getUnavailableException());
        }
        System.out.println("Caseboard is listening on http://127.0.0.1:" + port + "/");
        System.out.flush();
        server.join();
    }
}
