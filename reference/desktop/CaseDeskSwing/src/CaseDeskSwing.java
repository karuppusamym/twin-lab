import java.awt.BorderLayout;
import java.awt.FlowLayout;
import java.awt.GridLayout;
import java.awt.event.ActionEvent;
import java.awt.event.KeyEvent;
import java.util.ArrayList;
import java.util.List;
import javax.swing.*;
import javax.swing.table.DefaultTableModel;

/**
 * CaseDeskSwing: a stand-in "real" Java Swing desktop application for Twin Lab's desktop
 * recorder (Java Access Bridge path). Synthetic data only, in memory. Same flow as the WinForms
 * CaseDesk: sign in, search cases, open one, edit it, save (with a validation message).
 */
public final class CaseDeskSwing {
    static final String[] STATUSES = {"Open", "In progress", "Resolved", "Closed"};

    static final class Case {
        String caseNo, customer, summary, status, opened;
        boolean urgent;

        Case(String no, String customer, String summary, String status, String opened, boolean urgent) {
            this.caseNo = no; this.customer = customer; this.summary = summary;
            this.status = status; this.opened = opened; this.urgent = urgent;
        }
    }

    static final List<Case> CASES = new ArrayList<>(List.of(
        new Case("C-1001", "Ada Example", "Card not received", "Open", "2026-10-01", false),
        new Case("C-1002", "Ben Sample", "Address change", "In progress", "2026-10-03", false),
        new Case("C-1003", "Cleo Demo", "Fee refund request", "Open", "2026-10-07", true),
        new Case("C-2001", "Dan Synthetic", "Statement copy", "Closed", "2026-09-21", false)));

    public static void main(String[] args) {
        SwingUtilities.invokeLater(CaseDeskSwing::signIn);
    }

    static JPanel row(String label, JComponent field, char mnemonic) {
        JPanel p = new JPanel(new FlowLayout(FlowLayout.LEFT));
        JLabel l = new JLabel(label);
        l.setDisplayedMnemonic(mnemonic);
        l.setLabelFor(field);
        p.add(l);
        p.add(field);
        return p;
    }

    static void signIn() {
        JDialog d = new JDialog((JFrame) null, "Sign in", true);
        d.setName("LoginDialog");
        JTextField user = new JTextField(16);
        user.setName("txtUser");
        JPasswordField password = new JPasswordField(16);
        password.setName("txtPassword");
        JButton ok = new JButton("Sign in");
        ok.setName("btnSignIn");
        JButton cancel = new JButton("Cancel");
        cancel.setName("btnCancel");
        JPanel fields = new JPanel(new GridLayout(2, 1));
        fields.add(row("User name", user, 'U'));
        fields.add(row("Password", password, 'P'));
        JPanel buttons = new JPanel(new FlowLayout(FlowLayout.RIGHT));
        buttons.add(ok);
        buttons.add(cancel);
        d.getContentPane().add(fields, BorderLayout.CENTER);
        d.getContentPane().add(buttons, BorderLayout.SOUTH);
        d.getRootPane().setDefaultButton(ok);
        ok.addActionListener(e -> {
            if (user.getText().trim().isEmpty() || !"demo".equals(new String(password.getPassword()))) {
                JOptionPane.showMessageDialog(d, "Enter your user name and password.", "Sign in",
                    JOptionPane.WARNING_MESSAGE);
                return;
            }
            d.dispose();
            new MainWindow().setVisible(true);
        });
        cancel.addActionListener(e -> System.exit(0));
        d.setDefaultCloseOperation(WindowConstants.DISPOSE_ON_CLOSE);
        d.addWindowListener(new java.awt.event.WindowAdapter() {
            @Override public void windowClosed(java.awt.event.WindowEvent e) {
                if (!anyFrameVisible()) System.exit(0);
            }
        });
        d.pack();
        d.setLocationRelativeTo(null);
        d.setVisible(true);
    }

    static boolean anyFrameVisible() {
        for (java.awt.Frame f : java.awt.Frame.getFrames()) if (f.isVisible()) return true;
        return false;
    }

    static final class MainWindow extends JFrame {
        final JTextField search = new JTextField(18);
        final DefaultTableModel model = new DefaultTableModel(new String[] {"Case No", "Customer", "Status", "Opened"}, 0) {
            @Override public boolean isCellEditable(int r, int c) { return false; }
        };
        final JTable table = new JTable(model);
        final JLabel count = new JLabel();

        MainWindow() {
            super("CaseDesk - Cases");
            setName("MainWindow");
            setDefaultCloseOperation(EXIT_ON_CLOSE);
            JMenuBar bar = new JMenuBar();
            JMenu file = new JMenu("File");
            file.setMnemonic(KeyEvent.VK_F);
            JMenuItem exit = new JMenuItem("Exit", KeyEvent.VK_X);
            exit.addActionListener(e -> System.exit(0));
            file.add(exit);
            JMenu caseMenu = new JMenu("Case");
            caseMenu.setMnemonic(KeyEvent.VK_C);
            JMenuItem openCase = new JMenuItem("Open case", KeyEvent.VK_O);
            openCase.setAccelerator(KeyStroke.getKeyStroke(KeyEvent.VK_O, java.awt.event.InputEvent.CTRL_DOWN_MASK));
            openCase.addActionListener(e -> openSelected());
            JMenuItem refresh = new JMenuItem("Refresh", KeyEvent.VK_R);
            refresh.setAccelerator(KeyStroke.getKeyStroke(KeyEvent.VK_F5, 0));
            refresh.addActionListener(e -> runSearch());
            caseMenu.add(openCase);
            caseMenu.add(refresh);
            bar.add(file);
            bar.add(caseMenu);
            setJMenuBar(bar);

            search.setName("txtSearch");
            JButton find = new JButton("Search");
            find.setName("btnSearch");
            JButton open = new JButton("Open");
            open.setName("btnOpen");
            JPanel top = row("Case or name", search, 'N');
            top.add(find);
            top.add(open);
            table.setName("tblCases");
            table.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
            getContentPane().add(top, BorderLayout.NORTH);
            getContentPane().add(new JScrollPane(table), BorderLayout.CENTER);
            getContentPane().add(count, BorderLayout.SOUTH);
            getRootPane().setDefaultButton(find);
            find.addActionListener(e -> runSearch());
            open.addActionListener(e -> openSelected());
            runSearch();
            setSize(560, 360);
            setLocationRelativeTo(null);
        }

        void runSearch() {
            String text = search.getText().trim().toLowerCase();
            model.setRowCount(0);
            for (Case c : CASES) {
                if (text.isEmpty() || c.caseNo.toLowerCase().contains(text) || c.customer.toLowerCase().contains(text)) {
                    model.addRow(new Object[] {c.caseNo, c.customer, c.status, c.opened});
                }
            }
            if (model.getRowCount() > 0) table.setRowSelectionInterval(0, 0);
            count.setText(model.getRowCount() + (model.getRowCount() == 1 ? " case" : " cases"));
        }

        void openSelected() {
            int r = table.getSelectedRow();
            if (r < 0) {
                JOptionPane.showMessageDialog(this, "Select a case first.", "CaseDesk", JOptionPane.INFORMATION_MESSAGE);
                return;
            }
            String no = (String) model.getValueAt(r, 0);
            for (Case c : CASES) if (c.caseNo.equals(no)) {
                if (new DetailDialog(this, c).edit()) runSearch();
                return;
            }
        }
    }

    static final class DetailDialog extends JDialog {
        boolean saved;

        DetailDialog(JFrame owner, Case c) {
            super(owner, "Case " + c.caseNo, true);
            setName("CaseDetailDialog");
            JTextField no = new JTextField(c.caseNo, 18);
            no.setEditable(false);
            JTextField customer = new JTextField(c.customer, 18);
            JTextField summary = new JTextField(c.summary, 18);
            JComboBox<String> status = new JComboBox<>(STATUSES);
            status.setSelectedItem(c.status);
            JCheckBox urgent = new JCheckBox("Urgent", c.urgent);
            JButton save = new JButton("Save");
            JButton cancel = new JButton("Cancel");
            JPanel fields = new JPanel(new GridLayout(5, 1));
            fields.add(row("Case No", no, '\0'));
            fields.add(row("Customer", customer, 'C'));
            fields.add(row("Summary", summary, 'M'));
            fields.add(row("Status", status, 'T'));
            JPanel u = new JPanel(new FlowLayout(FlowLayout.LEFT));
            u.add(urgent);
            fields.add(u);
            JPanel buttons = new JPanel(new FlowLayout(FlowLayout.RIGHT));
            buttons.add(save);
            buttons.add(cancel);
            getContentPane().add(fields, BorderLayout.CENTER);
            getContentPane().add(buttons, BorderLayout.SOUTH);
            save.addActionListener((ActionEvent e) -> {
                String st = (String) status.getSelectedItem();
                String sum = summary.getText().trim();
                if (customer.getText().trim().isEmpty()) {
                    JOptionPane.showMessageDialog(this, "Customer is required.", "Case", JOptionPane.WARNING_MESSAGE);
                    return;
                }
                if (("Resolved".equals(st) || "Closed".equals(st)) && sum.length() < 10) {
                    JOptionPane.showMessageDialog(this,
                        "A resolved or closed case needs a summary of at least 10 characters.", "Case",
                        JOptionPane.WARNING_MESSAGE);
                    return;
                }
                c.customer = customer.getText().trim();
                c.summary = sum;
                c.status = st;
                c.urgent = urgent.isSelected();
                saved = true;
                dispose();
            });
            cancel.addActionListener(e -> dispose());
            pack();
            setLocationRelativeTo(owner);
        }

        boolean edit() {
            setVisible(true);
            return saved;
        }
    }
}
