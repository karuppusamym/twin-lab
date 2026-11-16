// Job pages poll until the job ends; buttons are disabled once pressed to avoid double submits.
(function () {
  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      e.preventDefault();
      return;
    }
    form.querySelectorAll("button[type=submit], button:not([type])").forEach(function (b) {
      setTimeout(function () { b.disabled = true; b.textContent = b.dataset.busy || "Working…"; }, 0);
    });
  });

  const job = document.querySelector("[data-job]");
  if (!job) return;
  const id = job.dataset.job;
  const log = document.getElementById("job-log");
  const status = document.getElementById("job-status");
  function tick() {
    fetch("/jobs/" + id + ".json", { headers: { accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        log.textContent = j.log || "";
        log.scrollTop = log.scrollHeight;
        status.textContent = j.status;
        status.className = "pill " + (j.status === "done" ? "ok" : j.status === "failed" || j.status === "interrupted" ? "err" : "warn");
        if (j.status === "queued" || j.status === "running") {
          setTimeout(tick, 1500);
        } else {
          document.getElementById("job-done").hidden = false;
        }
      })
      .catch(function () { setTimeout(tick, 3000); });
  }
  tick();
})();
