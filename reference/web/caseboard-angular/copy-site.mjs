// Copies the production build into site/ (committed, so the application runs without npm).
import { copyFileSync, cpSync, rmSync } from 'node:fs';

rmSync('site', { recursive: true, force: true });
cpSync('dist/caseboard/browser', 'site', { recursive: true });
copyFileSync('dist/caseboard/3rdpartylicenses.txt', 'site/3rdpartylicenses.txt');
console.log('site/ updated from dist/caseboard');
