# Third-party notices

The committed build in `site/` contains code from the packages below, bundled by the Angular
build. Their full licence texts are in [`site/3rdpartylicenses.txt`](site/3rdpartylicenses.txt),
which the build writes and which is kept with the build.

| Package | Version | Licence | Copyright |
|---|---|---|---|
| `@angular/core` | 21.2.26 | MIT | Google LLC |
| `@angular/common` | 21.2.26 | MIT | Google LLC |
| `@angular/forms` | 21.2.26 | MIT | Google LLC |
| `@angular/platform-browser` | 21.2.26 | MIT | Google LLC |
| `@angular/router` | 21.2.26 | MIT | Google LLC |
| `rxjs` | 7.8.2 | Apache-2.0 | Google, Inc., Netflix, Inc., Microsoft Corp. and contributors |
| `tslib` | 2.9.1 | 0BSD | Microsoft Corporation |

Angular is a project of Google LLC (<https://angular.dev/license>).

Used to build, not distributed here (installed by `npm ci` from the public npm registry, never
committed): `@angular/build`, `@angular/cli` and `@angular/compiler-cli` (MIT), `@angular/compiler`
(MIT), `typescript` (Apache-2.0), and their dependencies as pinned in `package-lock.json`.

The application's own source (`src/`), `serve.py`, `flow.py` and `certify_local.py` are part of
this repository. All data in the application is synthetic.
