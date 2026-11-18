const fs = require('fs');
fs.mkdirSync('static/vendor', { recursive: true });
for (const name of ['react', 'react-dom']) {
  fs.copyFileSync(`node_modules/${name}/umd/${name}.production.min.js`, `static/vendor/${name}.js`);
}
