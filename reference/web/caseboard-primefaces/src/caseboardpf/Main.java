package caseboardpf;

import java.io.File;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.EnumSet;

import javax.servlet.DispatcherType;
import javax.servlet.Filter;
import javax.servlet.FilterChain;
import javax.servlet.FilterConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.eclipse.jetty.server.Server;
import org.eclipse.jetty.servlet.ServletHolder;
import org.eclipse.jetty.webapp.WebAppContext;

/** Starts Caseboard PF on embedded Jetty: {@code java caseboardpf.Main --port 8139 --webapp webapp}. */
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

    /**
     * The owner picker (a dialog-framework page shown in an iframe) answers its first request
     * after 900 ms, as a slow office server does: a recorder that takes the snapshot as soon as
     * the ajax answer is in misses the page the dialog shows.
     */
    public static final class SlowPicker implements Filter {
        @Override
        public void init(FilterConfig config) { }

        @Override
        public void doFilter(ServletRequest req, ServletResponse resp, FilterChain chain)
                throws IOException, ServletException {
            if ("GET".equals(((HttpServletRequest) req).getMethod())) {
                try {
                    Thread.sleep(900);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            chain.doFilter(req, resp);
        }

        @Override
        public void destroy() { }
    }

    public static void main(String[] args) throws Exception {
        int port = 8139;
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
        context.setParentLoaderPriority(true); // the application, JSF and PrimeFaces are on the class path
        context.addServlet(new ServletHolder(new Home()), "");
        context.addFilter(SlowPicker.class, "/picker.xhtml", EnumSet.of(DispatcherType.REQUEST));
        server.setHandler(context);
        server.start();
        if (context.getUnavailableException() != null) {
            server.stop();
            throw new IllegalStateException(context.getUnavailableException());
        }
        System.out.println("Caseboard PF is listening on http://127.0.0.1:" + port + "/");
        System.out.flush();
        server.join();
    }
}
