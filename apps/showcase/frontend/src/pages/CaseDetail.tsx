import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CaseDetail, Note, Option } from "../api";

const EDITABLE = ["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED"];
const NOTE_MAX = 500;

type Message = { kind: "success" | "error"; text: string } | null;

export default function CaseDetailPage() {
  const { caseNo = "" } = useParams();
  const [item, setItem] = useState<CaseDetail | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [categories, setCategories] = useState<Option[]>([]);
  const [subcategories, setSubcategories] = useState<Option[]>([]);
  const [categoryId, setCategoryId] = useState("");
  const [subcategoryId, setSubcategoryId] = useState("");
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [noteText, setNoteText] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([
      api<CaseDetail>("GET", `/api/cases/${caseNo}`),
      api<Note[]>("GET", `/api/cases/${caseNo}/notes`),
      api<Option[]>("GET", "/api/categories"),
    ])
      .then(([c, n, cats]) => {
        if (!live) return;
        setItem(c);
        setNotes(n);
        setCategories(cats);
        setCategoryId(c.category_id ? String(c.category_id) : "");
        setSubcategoryId(c.subcategory_id ? String(c.subcategory_id) : "");
        setStatus(c.status);
        setReason(c.escalation_reason ?? "");
      })
      .catch((err: Error) => live && setLoadError(err.message));
    return () => {
      live = false;
    };
  }, [caseNo]);

  // Cascade: the sub-category list is reloaded whenever the category changes.
  useEffect(() => {
    if (!categoryId) {
      setSubcategories([]);
      return;
    }
    let live = true;
    api<Option[]>("GET", `/api/categories/${categoryId}/subcategories`).then(
      (subs) => live && setSubcategories(subs),
    );
    return () => {
      live = false;
    };
  }, [categoryId]);

  if (loadError)
    return (
      <div id="message" className="message error" role="alert">
        {loadError}
      </div>
    );
  if (!item)
    return (
      <div id="loading" className="muted">
        Loading case…
      </div>
    );

  const closed = item.status === "CLOSED";

  async function save(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    try {
      const updated = await api<CaseDetail>("PUT", `/api/cases/${caseNo}`, {
        status,
        category_id: Number(categoryId),
        subcategory_id: Number(subcategoryId),
        escalation_reason: status === "ESCALATED" ? reason : null,
      });
      setItem(updated);
      setMessage({ kind: "success", text: "Case updated" });
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    }
  }

  async function addNote(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    try {
      await api("POST", `/api/cases/${caseNo}/notes`, { text: noteText });
      setNotes(await api<Note[]>("GET", `/api/cases/${caseNo}/notes`));
      setNoteText("");
      setMessage({ kind: "success", text: "Note added" });
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    }
  }

  return (
    <>
      <p>
        <Link to="/cases" id="backToSearch">
          ← Back to search
        </Link>
      </p>
      <h1>
        Case <span id="fCaseNo">{item.case_no}</span>{" "}
        <span id="fStatus" className={`pill s-${item.status}`}>
          {item.status}
        </span>
      </h1>
      <p id="fTitle" className="lead">
        {item.title}
      </p>

      {message && (
        <div id="message" className={`message ${message.kind}`} role="status">
          {message.text}
        </div>
      )}

      <div className="columns">
        <section className="card">
          <h2>Customer</h2>
          <dl>
            <dt>Name</dt>
            <dd id="fCustomerName">{item.customer.full_name}</dd>
            <dt>Customer no.</dt>
            <dd id="fCustNo">{item.customer.cust_no}</dd>
            <dt>Email</dt>
            <dd id="fEmail">{item.customer.email}</dd>
            <dt>Phone</dt>
            <dd id="fPhone">{item.customer.phone}</dd>
            <dt>City</dt>
            <dd id="fCity">{item.customer.city}</dd>
            <dt>Priority</dt>
            <dd id="fPriority">{item.priority}</dd>
            <dt>Category</dt>
            <dd id="fCategory">
              {item.category ? `${item.category} / ${item.subcategory}` : "Not set"}
            </dd>
          </dl>
        </section>

        <section className="card">
          <h2>Update case</h2>
          {closed ? (
            <p id="closedNotice" className="muted">
              This case is closed and cannot be changed.
            </p>
          ) : (
            <form onSubmit={save} id="updateForm">
              <label htmlFor="category">Category</label>
              <select
                id="category"
                name="category"
                value={categoryId}
                onChange={(e) => {
                  setCategoryId(e.target.value);
                  setSubcategoryId("");
                }}
              >
                <option value="">Select…</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>

              <label htmlFor="subcategory">Sub-category</label>
              <select
                id="subcategory"
                name="subcategory"
                value={subcategoryId}
                disabled={!categoryId}
                onChange={(e) => setSubcategoryId(e.target.value)}
              >
                <option value="">Select…</option>
                {subcategories.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>

              <label htmlFor="caseStatus">Status</label>
              <select
                id="caseStatus"
                name="caseStatus"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {EDITABLE.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>

              {status === "ESCALATED" && (
                <>
                  <label htmlFor="escalationReason">Escalation reason</label>
                  <input
                    id="escalationReason"
                    name="escalationReason"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </>
              )}

              <button id="btnSave" type="submit" disabled={!categoryId || !subcategoryId}>
                Save
              </button>
            </form>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Notes</h2>
        {notes.length === 0 ? (
          <p id="noNotes" className="muted">
            No notes yet
          </p>
        ) : (
          <ul id="notes">
            {notes.map((n) => (
              <li key={n.id} data-note-id={n.id}>
                <span className="note-text">{n.text}</span>
                <span className="muted note-author"> — {n.author}</span>
              </li>
            ))}
          </ul>
        )}
        {!closed && (
          <form onSubmit={addNote} id="noteForm">
            <label htmlFor="noteText">Add a note</label>
            <textarea
              id="noteText"
              name="noteText"
              rows={3}
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
            />
            <div className="row">
              <button id="btnAddNote" type="submit" disabled={!noteText.trim()}>
                Add note
              </button>
              <span id="noteCount" className="muted">
                {noteText.length} / {NOTE_MAX}
              </span>
            </div>
          </form>
        )}
      </section>
    </>
  );
}
