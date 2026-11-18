/* Reconstructed from the public-demo recording; React 16.7.0, synthetic data only. */
(function () {
  'use strict';
  const h = React.createElement;
  const storageKey = 'twinlab-web-todo';
  class TodoApp extends React.Component {
    constructor(props) {
      super(props);
      let tasks = [];
      try { tasks = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch (_) {}
      if (!Array.isArray(tasks)) tasks = [];
      this.state = { tasks, draft: '', editing: null, editText: '', filter: location.hash, storageError: false };
      this.onHash = () => this.setState({ filter: location.hash });
    }
    componentDidMount() { window.addEventListener('hashchange', this.onHash); }
    componentWillUnmount() { window.removeEventListener('hashchange', this.onHash); }
    save(tasks, extra = {}) {
      let storageError = false;
      try { localStorage.setItem(storageKey, JSON.stringify(tasks)); } catch (_) { storageError = true; }
      this.setState({ tasks, storageError, ...extra });
    }
    remove(id) { this.save(this.state.tasks.filter(t => t.id !== id)); }
    finish(id) {
      if (this.state.editing !== id) return;
      const title = this.state.editText.trim();
      this.save(title ? this.state.tasks.map(t => t.id === id ? { ...t, title } : t) : this.state.tasks.filter(t => t.id !== id), { editing: null });
    }
    render() {
      const s = this.state;
      const active = s.tasks.filter(t => !t.completed).length;
      const completed = s.tasks.length - active;
      const visible = s.tasks.filter(t => s.filter === '#/active' ? !t.completed : s.filter === '#/completed' ? t.completed : true);
      return h('div', null,
        h('header', { className: 'header' }, h('h1', null, 'todos'),
          h('input', { className: 'new-todo', placeholder: 'What needs to be done?', value: s.draft, autoFocus: true,
            onChange: e => this.setState({ draft: e.target.value }),
            onKeyDown: e => {
              if (e.key !== 'Enter' || !s.draft.trim()) return;
              this.save([...s.tasks, { id: Date.now() + '-' + Math.random().toString(36).slice(2), title: s.draft.trim(), completed: false }], { draft: '' });
            } })),
        s.storageError && h('p', { role: 'alert' }, 'Browser storage is unavailable. Changes will be lost on reload.'),
        s.tasks.length > 0 && h('section', { className: 'main' },
          h('input', { id: 'toggle-all', className: 'toggle-all', type: 'checkbox', checked: active === 0,
            onChange: e => this.save(s.tasks.map(t => ({ ...t, completed: e.target.checked }))) }),
          h('label', { htmlFor: 'toggle-all' }, 'Mark all as complete'),
          h('ul', { className: 'todo-list' }, visible.map(t => h('li', { key: t.id, 'data-testid': 'todo-item',
            className: [t.completed ? 'completed' : '', s.editing === t.id ? 'editing' : ''].filter(Boolean).join(' ') },
            h('div', { className: 'view' },
              h('input', { 'aria-label': 'Toggle Todo', className: 'toggle', type: 'checkbox', checked: t.completed,
                onChange: e => this.save(s.tasks.map(x => x.id === t.id ? { ...x, completed: e.target.checked } : x)) }),
              h('label', { 'data-testid': 'todo-title', onDoubleClick: () => this.setState({ editing: t.id, editText: t.title }, () => {
                const input = document.querySelector('.editing .edit'); input.focus(); input.select();
              }) }, t.title),
              h('button', { 'aria-label': 'Delete', className: 'destroy', onClick: () => this.remove(t.id) })),
            h('input', { 'aria-label': 'Edit', className: 'edit', value: s.editing === t.id ? s.editText : t.title,
              onChange: e => this.setState({ editText: e.target.value }), onBlur: () => this.finish(t.id),
              onKeyDown: e => {
                if (e.key === 'Enter') this.finish(t.id);
                if (e.key === 'Escape') this.setState({ editing: null });
              } }) )))),
        s.tasks.length > 0 && h('footer', { className: 'footer' },
          h('span', { 'data-testid': 'todo-count', className: 'todo-count' }, h('strong', null, active), active === 1 ? ' item left' : ' items left'),
          h('ul', { className: 'filters' }, ['All', 'Active', 'Completed'].map((name, index) => {
            const href = index === 0 ? '#/' : '#/' + name.toLowerCase();
            const selected = index === 0 ? !['#/active', '#/completed'].includes(s.filter) : s.filter === href;
            return h(React.Fragment, { key: name }, index > 0 ? ' ' : null, h('li', null, h('a', { href, className: selected ? 'selected' : '' }, name)));
          })),
          completed > 0 && h('button', { className: 'clear-completed', onClick: () => this.save(s.tasks.filter(t => !t.completed)) }, 'Clear completed'))
      );
    }
  }
  ReactDOM.render(h(TodoApp), document.querySelector('.todoapp'));
}());
