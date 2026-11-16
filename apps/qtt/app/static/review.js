// QTT review screen: save checklist items, submit, return. Server-rendered page; this only
// calls the JSON API and redraws the parts the response changes.
(function () {
  "use strict";
  var reviewNo = document.getElementById("reviewNo").getAttribute("data-review-no");
  var base = "/api/reviews/" + encodeURIComponent(reviewNo);
  var message = document.getElementById("message");

  function $(id) { return document.getElementById(id); }

  function setMessage(text, ok) {
    message.textContent = text;
    message.className = "msg " + (ok ? "msg-ok" : "msg-error");
  }

  function call(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function errorOf(data) {
    var d = (data && data.detail) || {};
    return { code: d.code || "ERROR", message: d.message || "Something went wrong." };
  }

  function cell(text, cls) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text == null ? "" : text;
    return td;
  }

  function render(review) {
    var status = $("reviewStatus");
    status.textContent = review.status;
    status.className = "badge status-" + review.status.toLowerCase();
    $("progress").textContent =
      review.progress.answered + " of " + review.progress.total + " answered";
    $("lockNotice").hidden = !review.locked;
    var returned = review.status === "RETURNED" && !!review.return_reason;
    $("returnNotice").hidden = !returned;
    $("returnReasonText").textContent = returned ? review.return_reason : "";

    var editable = review.allowed.edit_items;
    review.items.forEach(function (item) {
      ["result-", "comment-", "save-"].forEach(function (p) {
        var el = $(p + item.item_id);
        if (el) el.disabled = !editable;
      });
    });

    var list = $("findings");
    list.innerHTML = "";
    review.findings.forEach(function (f) {
      var li = document.createElement("li");
      li.className = "finding";
      li.setAttribute("data-item-id", f.item_id);
      var strong = document.createElement("strong");
      strong.textContent = f.item_id;
      li.appendChild(strong);
      li.appendChild(document.createTextNode(" " + f.description + ": "));
      var span = document.createElement("span");
      span.className = "finding-comment";
      span.textContent = f.comment;
      li.appendChild(span);
      list.appendChild(li);
    });
    $("findingsCount").textContent = "(" + review.findings.length + ")";
    $("noFindings").hidden = review.findings.length > 0;

    var rows = $("auditRows");
    rows.innerHTML = "";
    review.audit.forEach(function (a) {
      var tr = document.createElement("tr");
      tr.className = "audit-row";
      tr.appendChild(cell(a.created_at, "when"));
      tr.appendChild(cell(a.event, "event"));
      tr.appendChild(cell(a.from_status));
      tr.appendChild(cell(a.to_status));
      tr.appendChild(cell(a.actor, "actor"));
      tr.appendChild(cell(a.detail, "detail"));
      rows.appendChild(tr);
    });

    if ($("btnSubmit")) $("btnSubmit").disabled = !review.allowed.submit;
    if ($("btnReopen")) $("btnReopen").disabled = !review.allowed.reopen;
    if ($("btnApprove")) $("btnApprove").disabled = !review.allowed.approve;
    if ($("btnReturn")) $("btnReturn").disabled = !review.allowed["return"];
    if ($("returnReason")) $("returnReason").disabled = !review.allowed["return"];
  }

  function markRequired(code) {
    var select = $("result-" + code);
    var comment = $("comment-" + code);
    var fail = select.value === "FAIL";
    comment.required = fail;
    comment.classList.toggle("required", fail);
  }

  Array.prototype.forEach.call(document.querySelectorAll("tr.check-item"), function (row) {
    var code = row.getAttribute("data-item-id");
    var select = $("result-" + code);
    var status = $("itemStatus-" + code);
    select.addEventListener("change", function () { markRequired(code); });
    $("save-" + code).addEventListener("click", function () {
      status.textContent = "";
      status.className = "item-status";
      call("PUT", base + "/items/" + encodeURIComponent(code), {
        result: select.value,
        comment: $("comment-" + code).value
      }).then(function (res) {
        if (res.ok) {
          render(res.data);
          status.textContent = "Saved";
          status.className = "item-status ok";
          setMessage(code + " saved.", true);
        } else {
          var err = errorOf(res.data);
          status.textContent = err.code;
          status.className = "item-status error";
          setMessage(err.message, false);
        }
      });
    });
  });

  var submit = $("btnSubmit");
  if (submit) {
    submit.addEventListener("click", function () {
      call("POST", base + "/submit", {}).then(function (res) {
        if (res.ok) {
          render(res.data);
          setMessage("Review " + reviewNo + " submitted for approval.", true);
        } else {
          setMessage(errorOf(res.data).message, false);
        }
      });
    });
  }

  var ret = $("btnReturn");
  if (ret) {
    ret.addEventListener("click", function () {
      call("POST", base + "/return", { reason: $("returnReason").value }).then(function (res) {
        if (res.ok) {
          render(res.data);
          setMessage("Review " + reviewNo + " returned to the analyst.", true);
        } else {
          setMessage(errorOf(res.data).message, false);
        }
      });
    });
  }
})();
