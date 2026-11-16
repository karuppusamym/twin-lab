import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

interface CannedResponse {
  id: number;
  code: string;
  title: string;
  body: string;
  active: boolean;
}

/** Team-lead screen for reply templates. No automation uses it. */
export default function ResponsesPage() {
  const [rows, setRows] = useState<CannedResponse[]>([]);
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);

  async function load() {
    setRows(await api<CannedResponse[]>("GET", "/api/responses"));
  }

  useEffect(() => {
    load();
  }, []);

  async function add(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    try {
      await api("POST", "/api/responses", { code, title, body });
      setCode("");
      setTitle("");
      setBody("");
      setMessage({ kind: "success", text: "Response added" });
      await load();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    }
  }

  return (
    <>
      <h1>Canned responses</h1>
      {message && (
        <div id="responseMessage" className={`message ${message.kind}`} role="status">
          {message.text}
        </div>
      )}
      <div className="card scroll">
        <table id="responses">
          <thead>
            <tr>
              <th>Code</th>
              <th>Title</th>
              <th>Text</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="code">{r.code}</td>
                <td>{r.title}</td>
                <td>{r.body}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="card" onSubmit={add} id="responseForm">
        <h2>Add a response</h2>
        <label htmlFor="respCode">Code</label>
        <input id="respCode" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
        <label htmlFor="respTitle">Title</label>
        <input id="respTitle" value={title} onChange={(e) => setTitle(e.target.value)} />
        <label htmlFor="respBody">Text</label>
        <textarea id="respBody" rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
        <button id="btnAddResponse" type="submit" disabled={!code || !title || !body}>
          Add response
        </button>
      </form>
    </>
  );
}
