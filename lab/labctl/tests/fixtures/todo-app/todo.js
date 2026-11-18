// A small to-do list written for Twin Lab's capture and replay tests (not TodoMVC's code).
// Deterministic: tasks get ids 1, 2, 3...; everything is kept in localStorage under "tl-todos".
(function () {
  "use strict";
  var KEY = "tl-todos";
  var todos = load();
  var editing = null;
  var list = document.querySelector(".todo-list");
  var input = document.querySelector(".new-todo");
  var main = document.querySelector(".main");
  var footer = document.querySelector(".footer");
  var toggleAll = document.getElementById("toggle-all");

  function load() {
    try {
      var data = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(data) ? data : [];
    } catch (e) {
      return [];
    }
  }
  function save() { localStorage.setItem(KEY, JSON.stringify(todos)); }
  function nextId() { return todos.reduce(function (m, t) { return Math.max(m, t.id); }, 0) + 1; }
  function filter() {
    var h = location.hash;
    return h === "#/active" ? "active" : h === "#/completed" ? "completed" : "all";
  }
  function find(id) { return todos.filter(function (t) { return t.id === id; })[0]; }

  function row(todo) {
    var li = document.createElement("li");
    if (todo.completed) li.className = "completed";
    if (editing === todo.id) li.className += (li.className ? " " : "") + "editing";
    li.dataset.key = String(todo.id);
    var view = document.createElement("div");
    view.className = "view";
    var box = document.createElement("input");
    box.className = "toggle";
    box.type = "checkbox";
    box.checked = !!todo.completed;
    var label = document.createElement("label");
    label.textContent = todo.title;
    var destroy = document.createElement("button");
    destroy.className = "destroy";
    destroy.setAttribute("aria-label", "Delete");
    view.appendChild(box);
    view.appendChild(label);
    view.appendChild(destroy);
    var edit = document.createElement("input");
    edit.className = "edit";
    edit.value = todo.title;
    li.appendChild(view);
    li.appendChild(edit);
    return li;
  }

  function render() {
    var f = filter();
    list.textContent = "";
    todos.forEach(function (t) {
      if (f === "active" && t.completed) return;
      if (f === "completed" && !t.completed) return;
      list.appendChild(row(t));
    });
    var left = todos.filter(function (t) { return !t.completed; }).length;
    var done = todos.length - left;
    main.hidden = footer.hidden = todos.length === 0;
    toggleAll.checked = todos.length > 0 && left === 0;
    document.querySelector(".todo-count").innerHTML =
      "<strong>" + left + "</strong> " + (left === 1 ? "item" : "items") + " left";
    document.querySelector(".clear-completed").hidden = done === 0;
    Array.prototype.forEach.call(document.querySelectorAll(".filters a"), function (a) {
      a.className = a.getAttribute("href") === "#/" + (f === "all" ? "" : f) ? "selected" : "";
    });
    if (editing !== null) {
      var field = list.querySelector("li.editing .edit");
      if (field) {
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
      }
    }
  }

  function keyOf(el) {
    var li = el.closest("li");
    return li ? Number(li.dataset.key) : null;
  }
  function finishEdit(field, keep) {
    var id = editing;
    if (id === null) return;
    editing = null;
    var todo = find(id);
    if (todo && keep) {
      var title = field.value.trim();
      if (title) todo.title = title;
      else todos = todos.filter(function (t) { return t.id !== id; });
      save();
    }
    render();
  }

  input.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var title = input.value.trim();
    if (!title) return; // a blank task is rejected
    todos.push({ id: nextId(), title: title, completed: false });
    input.value = "";
    save();
    render();
  });
  toggleAll.addEventListener("change", function () {
    var on = toggleAll.checked;
    todos.forEach(function (t) { t.completed = on; });
    save();
    render();
  });
  list.addEventListener("change", function (e) {
    if (!e.target.classList.contains("toggle")) return;
    var todo = find(keyOf(e.target));
    if (!todo) return;
    todo.completed = e.target.checked;
    save();
    render();
  });
  list.addEventListener("click", function (e) {
    if (!e.target.classList.contains("destroy")) return;
    var id = keyOf(e.target);
    todos = todos.filter(function (t) { return t.id !== id; });
    save();
    render();
  });
  list.addEventListener("dblclick", function (e) {
    if (e.target.tagName !== "LABEL") return;
    editing = keyOf(e.target);
    render();
  });
  list.addEventListener("keydown", function (e) {
    if (!e.target.classList.contains("edit")) return;
    if (e.key === "Enter") finishEdit(e.target, true);
    else if (e.key === "Escape") finishEdit(e.target, false);
  });
  list.addEventListener("focusout", function (e) {
    if (e.target.classList.contains("edit") && editing !== null) finishEdit(e.target, true);
  });
  document.querySelector(".clear-completed").addEventListener("click", function () {
    todos = todos.filter(function (t) { return !t.completed; });
    save();
    render();
  });
  window.addEventListener("hashchange", render);
  render();
})();
