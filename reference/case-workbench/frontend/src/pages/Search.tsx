import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, CaseRow } from "../api";

const STATUSES = ["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED"];

export default function SearchPage() {
  const [caseNo, setCaseNo] = useState("");
  const [customer, setCustomer] = useState("");
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<CaseRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function search(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const q = new URLSearchParams({ caseNo, customer, status });
      const r = await api<{ items: CaseRow[] }>("GET", `/api/cases/search?${q}`);
      setRows(r.items);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Find a case</h1>
      <form className="card filters" onSubmit={search} id="searchForm">
        <div>
          <label htmlFor="caseNo">Case number</label>
          <input id="caseNo" name="caseNo" value={caseNo} onChange={(e) => setCaseNo(e.target.value)} />
        </div>
        <div>
          <label htmlFor="customerName">Customer name</label>
          <input
            id="customerName"
            name="customerName"
            value={customer}
            onChange={(e) => setCustomer(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="status">Status</label>
          <select id="status" name="status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <button id="btnSearch" type="submit" disabled={busy}>
          Search
        </button>
      </form>

      {error && (
        <div className="message error" role="alert">
          {error}
        </div>
      )}
      {busy && (
        <div id="loading" className="muted">
          Searching…
        </div>
      )}
      {!busy && rows && (
        <div className="card">
          <div id="resultCount" className="muted">
            {rows.length === 0 ? "No cases found" : `${rows.length} case(s)`}
          </div>
          {rows.length > 0 && (
            <div className="scroll">
              <table id="results">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Customer</th>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.case_no} data-case-no={r.case_no}>
                      <td className="case-no">
                        <Link className="case-link" to={`/cases/${r.case_no}`}>
                          {r.case_no}
                        </Link>
                      </td>
                      <td className="customer">{r.customer}</td>
                      <td className="title">{r.title}</td>
                      <td className="status">
                        <span className={`pill s-${r.status}`}>{r.status}</span>
                      </td>
                      <td className="priority">{r.priority}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  );
}
