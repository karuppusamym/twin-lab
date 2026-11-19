// The administration screens of the parcel depot: a lazy chunk the recorded flow never opens.

export const AdminPage = {
  render() {
    return `<h1>Administration</h1>
      <div role="tablist" id="adminTabs">
        <a role="tab" id="tabUsers" href="/admin/users" data-link>Users</a>
        <a role="tab" id="tabSettings" href="/admin/settings" data-link>Settings</a>
      </div>
      <section role="tabpanel" id="parcelPanel"></section>`;
  },
};

export const AdminUsers = {
  async load() {
    const r = await fetch('/api/admin/users');
    const rows = await r.json();
    return `<table id="users"><thead><tr><th>User</th><th>Role</th></tr></thead><tbody>${rows.items
      .map((u) => `<tr><td>${u.name}</td><td>${u.role}</td></tr>`).join('')}</tbody></table>`;
  },
};

export const AdminSettings = {
  load() {
    return `<form id="settingsForm"><label for="cutoff">Cut-off hour</label><input id="cutoff">
      <button id="saveSettings" type="button">Save settings</button></form>`;
  },
  async save(value) {
    await fetch('/api/admin/settings', { method: 'PUT', body: JSON.stringify({ cutoff: value }) });
  },
};
