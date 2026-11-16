import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { session } from "./api";
import CaseDetailPage from "./pages/CaseDetail";
import LoginPage from "./pages/Login";
import ResponsesPage from "./pages/Responses";
import SearchPage from "./pages/Search";
import "./styles.css";

function Shell() {
  const navigate = useNavigate();
  if (!session.token()) return <Navigate to="/login" replace />;
  return (
    <>
      <header className="topbar">
        <Link to="/cases" className="brand">
          Case Workbench
        </Link>
        <nav>
          <Link to="/cases" id="navCases">
            Cases
          </Link>
          <Link to="/admin/responses" id="navResponses">
            Canned responses
          </Link>
        </nav>
        <span className="who">
          <span id="currentUser">{session.name()}</span>
          <button
            type="button"
            id="btnLogout"
            className="link"
            onClick={() => {
              session.clear();
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Shell />}>
          <Route path="/cases" element={<SearchPage />} />
          <Route path="/cases/:caseNo" element={<CaseDetailPage />} />
          <Route path="/admin/responses" element={<ResponsesPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/cases" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
