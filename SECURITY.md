# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately with GitHub's
**Report a vulnerability** button on the repository's **Security** tab (private vulnerability
reporting). Include what you found, how to reproduce it, and what it could affect.

You will get an acknowledgement within five working days, and a plan or a fix as soon as the
problem is understood. Reports are credited in the changelog unless you ask otherwise.

## Supported versions

Only the latest `main` is supported. There are no maintained release branches yet.

## Sensitive data

Twin Lab handles recordings of real applications. If you find real customer data, credentials,
tokens or internal hostnames anywhere in this repository, in its history, or in a generated
package, report it the same private way so it can be removed and the secret revoked.

## What counts

- A way to make Twin Lab keep or leak typed values, cookies, tokens or other data its privacy
  rules say are dropped or masked.
- A way for a recording, build package or demo app to run code on, or reach hosts from, the
  machine running `labctl` beyond what is documented.
- Weaknesses in the lab agent, the portal, or the deployment files under `deploy/`.

Demo apps are meant to be look-alike copies with synthetic data; that they resemble a real
application is by design and not a vulnerability.
