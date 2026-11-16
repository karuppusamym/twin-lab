/* QTT review page: saves checklist items and decisions through the JSON API with fetch,
   then redraws the parts of the page that depend on the review (status, progress, findings,
   history, which controls are enabled). Plain JavaScript, no framework. */
(function () {
  "use strict";

  var reviewNo = document.getElementById("reviewNo").getAttribute("data-review-no");
  var base = "/api/reviews/" + encodeURIComponent(reviewNo);
  var messageBox = document.getElementById("message");

  function $(id) {
    return document.getElementById(id);
  }

  function showMessage(text, ok) {
    messageBox.textContent = text;
    messageBox.className = "msg " + (ok ? "msg-ok" : "msg-error");
  }

  function call(method, url, body) {
    var options = {
      method: method,
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          var detail = (data && data.detail) || {};
          var error = new Error(detail.message || "Request failed (" + response.status + ")");
          error.code = detail.code || "HTTP_" + response.status;
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function text(tag, value, className) {
    var el = document.createElement(tag);
    el.textContent = value == null ? "" : value;
    if (className) el.className = className;
    return el;
  }

  function render(review) {
    var status = $("reviewStatus");
    status.textContent = review.status;
    status.className = "badge status-" + review.status.toLowerCase();
    $("progress").textContent =
      review.progress.answered + " of " + review.progress.total + " answered";
    $("lockNotice").hidden = !review.locked;
    $("returnNotice").hidden = review.status !== "RETURNED";
    $("returnReasonText").textContent = review.return_reason || "";

    var findings = $("findings");
    findings.innerHTML = "";
    review.findings.forEach(function (f) {
      var li = document.createElement("li");
      li.className = "finding";
      li.setAttribute("data-item-id", f.item_id);
      li.appendChild(text("strong", f.item_id));
      li.appendChild(document.createTextNode(" " + f.description + ": "));
      li.appendChild(text("span", f.comment, "finding-comment"));
      findings.appendChild(li);
    });
    $("findingsCount").textContent = "(" + review.findings.length + ")";
    $("noFindings").hidden = review.findings.length > 0;

    var rows = $("auditRows");
    rows.innerHTML = "";
    review.audit.forEach(function (a) {
      var tr = document.createElement("tr");
      tr.className = "audit-row";
      tr.appendChild(text("td", a.created_at, "when"));
      tr.appendChild(text("td", a.event, "event"));
      tr.appendChild(text("td", a.from_status));
      tr.appendChild(text("td", a.to_status));
      tr.appendChild(text("td", a.actor, "actor"));
      tr.appendChild(text("td", a.detail, "detail"));
      rows.appendChild(tr);
    });

    var can = review.allowed;
    review.items.forEach(function (item) {
      ["result-", "comment-", "save-"].forEach(function (prefix) {
        var el = $(prefix + item.item_id);
        if (el) el.disabled = !can.edit_items;
      });
    });
    if ($("btnSubmit")) $("btnSubmit").disabled = !can.submit;
    if ($("btnReopen")) $("btnReopen").hidden = !can.reopen;
    if ($("btnApprove")) $("btnApprove").disabled = !can.approve;
    if ($("btnReturn")) $("btnReturn").disabled = !can["return"];
    if ($("returnReason")) $("returnReason").disabled = !can["return"];
  }

  function markRequired(itemId) {
    var result = $("result-" + itemId);
    var comment = $("comment-" + itemId);
    var fail = result.value === "FAIL";
    comment.required = fail;
    comment.classList.toggle("required", fail);
    comment.placeholder = fail ? "Comment required for Fail" : "";
  }

  function saveItem(itemId) {
    var status = $("itemStatus-" + itemId);
    var result = $("result-" + itemId).value;
    var comment = $("comment-" + itemId).value;
    status.textContent = "Saving...";
    status.className = "item-status";
    return call("PUT", base + "/items/" + encodeURIComponent(itemId), {
      result: result || null,
      comment: comment
    }).then(
      function (review) {
        status.textContent = "Saved";
        status.className = "item-status ok";
        showMessage(itemId + " saved.", true);
        render(review);
      },
      function (error) {
        status.textContent = error.code;
        status.className = "item-status error";
        showMessage(error.message, false);
        if (error.code === "COMMENT_REQUIRED") $("comment-" + itemId).focus();
      }
    );
  }

  function decide(action, body, done) {
    return call("POST", base + "/" + action, body).then(
      function (review) {
        render(review);
        showMessage(done, true);
      },
      function (error) {
        showMessage(error.message, false);
      }
    );
  }

  document.querySelectorAll("tr.check-item").forEach(function (row) {
    var itemId = row.getAttribute("data-item-id");
    $("result-" + itemId).addEventListener("change", function () {
      markRequired(itemId);
    });
    $("save-" + itemId).addEventListener("click", function () {
      saveItem(itemId);
    });
  });

  if ($("btnSubmit")) {
    $("btnSubmit").addEventListener("click", function () {
      decide("submit", undefined, "Review " + reviewNo + " submitted for approval.");
    });
  }
  if ($("btnReopen")) {
    $("btnReopen").addEventListener("click", function () {
      decide("reopen", undefined, "Review " + reviewNo + " reopened.");
    });
  }
  if ($("btnApprove")) {
    $("btnApprove").addEventListener("click", function () {
      decide("approve", undefined, "Review " + reviewNo + " approved.");
    });
  }
  if ($("btnReturn")) {
    $("btnReturn").addEventListener("click", function () {
      decide(
        "return",
        { reason: $("returnReason").value },
        "Review " + reviewNo + " returned to the analyst."
      );
    });
  }
})();
