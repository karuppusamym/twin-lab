# Running the labs on OpenShift

Locally, each demo app runs in Docker Compose (see [office-setup.md](office-setup.md)). This guide
is for running the same lab on an OpenShift (OCP) cluster instead: a GitHub pipeline builds the
images, pushes them to GitHub's container registry (GHCR) and deploys each app's lab into one
OpenShift project.

> **Honest limits.** The manifests, the pipeline and the `labctl ocp` commands were written and
> tested without a real cluster: the tests check what is rendered, and the images were run locally
> in Docker the way OpenShift runs them (a random user ID in group 0, no capabilities). The first
> deploy to your cluster is the real test; read [Troubleshooting](#troubleshooting) before it.
> There is **one shared copy per app** in the project: everyone who uses it shares its data, its
> slowdowns and its recordings, as with one office lab machine.

## 1. What gets deployed

For each app (here `showcase`), in one project:

```
 browser / bot
      |
      | https://twinlab-showcase-web-<project>.<cluster domain>     (Route, TLS at the router)
      v
 +-----------------------------+        +----------------------------+
 | twinlab-showcase-agent      | -----> | twinlab-showcase-app       |
 | lab agent :9000             |  HTTP  | the demo app :8000         |
 |   front door (proxy)        |        +----------------------------+
 |   admin API /_lab/* (token) |                     |
 | DB link proxy :15432  <-----------------------------+  the app's database connection
 +-----------------------------+
      |                                  (state: PVC twinlab-showcase-agent-state, 1 GiB)
      v
 +-----------------------------+
 | twinlab-showcase-db         |   Postgres 16, PVC twinlab-showcase-db (5 GiB)
 +-----------------------------+
      ^
      |  Job twinlab-showcase-db-setup: sets the passwords, creates the `bot` login (rows only)
```

| Object | Name | What it is |
|---|---|---|
| Deployment, Service, PVC | `twinlab-<app>-db` | Postgres (`quay.io/sclorg/postgresql-16-c9s`, made for OpenShift) |
| Job, ConfigMap | `twinlab-<app>-db-setup` | sets up the database logins; runs again on every deploy, harmless |
| Deployment, Service | `twinlab-<app>-agent` | the lab agent: ports 9000 (front door) and 15432 (database link) |
| PVC | `twinlab-<app>-agent-state` | baselines and recorded sessions |
| ConfigMap | `twinlab-<app>-schema` | the app's `db/schema.sql` |
| Deployment, Service | `twinlab-<app>-app` | the demo app |
| Route | `twinlab-<app>-web` | the app's address; HTTPS to the agent's port 9000 only |
| Secret | `twinlab-<app>-secrets` | `lab-token`, `db-password`, `bot-password` (see below) |

Every object carries the labels `app.kubernetes.io/part-of: twinlab` and
`app.kubernetes.io/instance: <app>`, so one command lists an app's lab:

```bash
oc get all,pvc,route,configmap,job -l app.kubernetes.io/instance=showcase
```

Things to know:

- **The Secret** is created by the first deploy, with random values, and never overwritten after
  that. The manifests only name its keys, so the rendered YAML holds no password and can be shared.
- **The admin API** (`/_lab/*`: reset, baselines, slowdowns, sessions) answers only with the header
  `X-Lab-Token` set to the `lab-token` value. To read it:

  ```bash
  oc extract secret/twinlab-showcase-secrets --keys=lab-token --to=-
  ```

- **The database link (15432) is not on the Route.** Routes carry HTTP(S) only. Bots running inside
  the cluster use `twinlab-<app>-agent:15432`. From a desktop, for a test:

  ```bash
  oc port-forward svc/twinlab-showcase-agent 15432:15432
  ```

  Bots log in as `bot` with the `bot-password` value (rows only, no table changes).
- **Every container** runs as a non-root, random user ID chosen by OpenShift, with no extra
  capabilities, no privilege escalation and the default seccomp profile (the `restricted-v2`
  security context constraint). No special permission is needed.
- **Size.** Per app, requests are about 0.35 CPU and 0.7 GiB memory (limits: 3.5 CPU, 2.5 GiB), and
  6 GiB of storage. Check that the project's quota allows this for every app.

## 2. One-time setup

You need: an OpenShift project you can administer, the `oc` command line (from the cluster's web
console: **?** > **Command line tools**), and admin rights on the GitHub repository.

### 2.1 The project

```bash
oc login https://api.<your cluster>:6443
```

```bash
oc new-project twinlab
```

Any name works; it must be lower case letters, digits and `-`. Write it down: the pipeline needs it.

### 2.2 A service account for the pipeline

The pipeline signs in as a service account with the `edit` role in this project only.

```bash
oc create serviceaccount twinlab-deployer -n twinlab
```

```bash
oc policy add-role-to-user edit -z twinlab-deployer -n twinlab
```

A token for it (one year here; your cluster may cap the duration, then renew it when it expires):

```bash
oc create token twinlab-deployer -n twinlab --duration=8760h
```

If your cluster refuses long durations, ask the cluster admin for a long-lived service account
token Secret (type `kubernetes.io/service-account-token`) instead.

Keep the token safe: anyone holding it can change the project.

### 2.3 GitHub settings

In the repository: **Settings** > **Secrets and variables** > **Actions**.

| Kind | Name | Value |
|---|---|---|
| Secret | `OCP_SERVER` | the API address, e.g. `https://api.<your cluster>:6443` (`oc whoami --show-server`) |
| Secret | `OCP_TOKEN` | the token from 2.2 |
| Variable | `OCP_NAMESPACE` | the project name (default `twinlab` when not set) |
| Variable (optional) | `OCP_INSECURE_TLS` | `true` only if the cluster's certificates are self-signed |
| Variable (optional) | `OCP_PULL_SECRET` | the pull secret name, if you use one (2.4) |
| Variable (optional) | `OCP_EXPOSE_DB` | `true` to give bots outside the cluster the database link on a NodePort (section 7) |
| Secret (optional) | `TWINLAB_DATABASES` | the DBA-provided servers, as the `databases:` YAML of `twinlab.local.yaml` with passwords written in (section 2.5) |

The secrets can also be set on the **ocp** environment (**Settings** > **Environments** > **ocp**).
That environment is where to add **required reviewers**, so every deploy waits for an approval.
GitHub creates it on the first run if it does not exist.

Without `OCP_SERVER` and `OCP_TOKEN` the pipeline still builds and pushes the images, then shows a
notice that the deploy was skipped. It does not fail.

### 2.4 Letting OpenShift pull the images

The images go to GHCR under the repository:
`ghcr.io/<owner>/<repo>/lab-agent`, `ghcr.io/<owner>/<repo>/showcase`, `ghcr.io/<owner>/<repo>/qtt`.
New GHCR packages are private. Choose one:

**Make them public** (the repository is public, so the images hold nothing that is not already
public). After the first pipeline run, on GitHub: your profile > **Packages** > each package >
**Package settings** > **Change visibility** > **Public**. Do it once per package (`lab-agent` and
each app; a new app adds a new package).

**Or keep them private and add a pull secret.** Create a GitHub personal access token (classic)
with only `read:packages`, then:

```bash
oc create secret docker-registry ghcr-pull -n twinlab --docker-server=ghcr.io --docker-username=<github user> --docker-password=<token>
```

```bash
oc secrets link default ghcr-pull --for=pull -n twinlab
```

Linking it to the `default` service account is enough. Alternatively set the variable
`OCP_PULL_SECRET` to `ghcr-pull` and every pod names it.

### 2.5 Apps on a DBA's SQL Server, Oracle or Postgres server

An app whose `lab.database` names a server (see [office-setup.md](office-setup.md), "Which
database a demo app runs on") gets no database pod. Instead:

- the secret `TWINLAB_DATABASES` holds the servers (the pipeline reads it; by hand, set the same
  environment variable or have them in `twinlab.local.yaml`):

  ```yaml
  qtt-sqlserver: {engine: mssql, host: sqldemo01.office.local, database: TWINLAB_QTT, owner_user: tl_owner, owner_password: "...", bot_user: tl_bot, bot_password: "..."}
  ```

- each deploy writes the Secret `twinlab-<app>-dbconn` (owner and bot addresses, the app's
  connection string) from it, so changing a password is: update `TWINLAB_DATABASES`, deploy again;
- the Job `twinlab-<app>-db-tables` (in the lab-agent image) creates the tables from the app's
  `db/schema.sql`, leaving existing ones alone;
- the cluster must reach the server's port (ask for a firewall rule from the project's nodes).

No password is written in a manifest. The built-in `sqlserver` choice is for local labs only;
`labctl ocp render` refuses it.

## 3. How the pipeline runs

The workflow is `.github/workflows/deploy-ocp.yml`. It runs:

- on every push to `main` that changes `apps/**`, `lab/**` or the workflow itself. A push that
  changes only `apps/showcase/` deploys only showcase; a change to the lab agent (`lab/**`) or
  to this workflow deploys every built app; a push that touches no built app deploys nothing;
- by hand: **Actions** > **deploy-ocp** > **Run workflow**, with an app name, or empty for all.

Its jobs:

1. **plan**: lists the built apps (a `Dockerfile` and a `lab:` section in `app.yaml`).
2. **build-agent** and **build-app**: build and push `lab-agent` and each app, tagged with the commit
   SHA (`ghcr.io/<owner>/<repo>/<name>:<sha>`).
3. **deploy** (one per app, environment **ocp**): checks the secrets, installs `oc`, logs in, runs
   `labctl ocp deploy`, then a smoke check through the Route. On failure it prints the pods, the
   recent events and the logs.

`labctl ocp deploy` does, in order: render the manifests, create the Secret if it is missing, apply
everything, wait for the database, run the login setup Job, wait for the agent and the app, run the
setup Job again (it grants the `bot` login rows in tables the app created at start), and check the
Route. The first time, it also records the app's starting data as the baseline, so Reset works.

Only one deploy runs at a time; a newer push waits for the running one.

## 4. Deploying by hand

From a checkout, with `oc` logged in to the project. The simplest is the same command the pipeline
uses (the images for that commit must already be in GHCR):

```bash
labctl ocp deploy showcase --registry ghcr.io/<owner>/<repo> --tag <commit sha> --namespace twinlab
```

Add `--insecure-tls` if the Route's certificate is self-signed. Without `oc` on PATH it says so and
changes nothing.

To see or review the manifests first, render them to a folder (one file per object):

```bash
labctl ocp render showcase --registry ghcr.io/<owner>/<repo> --tag <commit sha> --namespace twinlab --out ocp-out/showcase
```

Then apply them with `oc` yourself. Create the Secret first if this is the first time (skip if
`oc get secret twinlab-showcase-secrets` finds it; never re-create it on a running lab):

```bash
oc create secret generic twinlab-showcase-secrets -n twinlab --from-literal=lab-token="$(openssl rand -hex 24)" --from-literal=db-password="$(openssl rand -hex 24)" --from-literal=bot-password="$(openssl rand -hex 24)"
```

A Job cannot be changed once created, so remove the previous setup Job before applying:

```bash
oc delete job twinlab-showcase-db-setup -n twinlab --ignore-not-found
```

```bash
oc apply -f ocp-out/showcase
```

```bash
oc rollout status deployment/twinlab-showcase-app -n twinlab
```

Check it:

```bash
labctl ocp smoke showcase --namespace twinlab
```

## 4a. Applying by hand, with no pipeline and no token on your machine

When the only thing you can do is check code into a branch, use the files under
`deploy/ocp/<app>/`. Each folder has numbered manifests and a `README.md` with the exact steps
for whoever has access to the project: create one Secret, apply the folder (or paste the files
into the console's Import YAML), start the two builds, check health through the Route. The
images are built inside the cluster from the branch, so no outside registry is needed.

```bash
labctl ocp render <app> --manual --git-ref <branch> --out deploy/ocp/<app>
```

```bash
labctl ocp check deploy/ocp/<app>
```

Commit the folder with the branch. `labctl ocp render --all --manual --out deploy/ocp` renders
every app. An app on SQLite, or one that keeps its data in the browser, runs as one pod. These
files have not been applied to a real cluster yet: the README lists what to watch on the first try.

## 5. Rolling back

Each deploy changes the image tag, so OpenShift keeps the previous versions. To go back one
version of the app and of the agent:

```bash
oc rollout undo deployment/twinlab-showcase-app -n twinlab
```

```bash
oc rollout undo deployment/twinlab-showcase-agent -n twinlab
```

To go to a known good commit instead, deploy its SHA by hand (section 4, `--tag <old sha>`); its
images are still in GHCR. The next pipeline run deploys the newest commit again.

Rollbacks do not touch the data: the database and the agent's baselines stay on their volumes. To
start an app's data from scratch, use **Reset** in the portal or the admin API; deleting the PVCs
deletes the data for good.

## 6. Troubleshooting

Start with:

```bash
oc get pods,jobs,pvc,route -l app.kubernetes.io/instance=showcase -n twinlab
```

```bash
oc get events -n twinlab --sort-by=.lastTimestamp
```

| What you see | Likely cause | What to do |
|---|---|---|
| `ImagePullBackOff` | the GHCR package is private | make it public or add the pull secret (2.4) |
| `ImagePullBackOff`, image not found | the build for that commit failed or was not pushed | check the build jobs in the pipeline run |
| PVC `Pending` | the cluster has no default storage class | ask the cluster admin for one, or for which class to use |
| Pods not created: `forbidden: exceeded quota` | the project quota is too small | raise the quota (section 1, Size) |
| `unable to validate against any security context constraint` | an image or setting needs more than `restricted-v2` | report it: the manifests are meant to need nothing more |
| Job `twinlab-<app>-db-setup` fails | the database is not up, or its password differs from the Secret | `oc logs job/twinlab-<app>-db-setup`; `oc logs deployment/twinlab-<app>-db` |
| Agent not ready, probe fails | the agent cannot start, or its token does not match | `oc logs deployment/twinlab-<app>-agent` |
| App restarts, health fails | the app cannot reach its database through the agent | `oc logs deployment/twinlab-<app>-app`; check the agent is running |
| Smoke check: certificate error | the router's certificate is self-signed | set variable `OCP_INSECURE_TLS` to `true`, or `--insecure-tls` by hand |
| Smoke check: 503 from `/_lab/health` | the agent runs but the app or the database is down | the body says which: `{"app": "down"}` or `{"db": "down"}` |
| Pipeline: "deploy skipped" notice | `OCP_SERVER` or `OCP_TOKEN` is not set | section 2.3 |
| Pipeline: `oc login` fails | the token expired or was revoked | create a new token (2.2) and update `OCP_TOKEN` |

Changing a password: edit the value in the Secret (`oc edit secret twinlab-<app>-secrets`), restart
the three Deployments (`oc rollout restart deployment -l app.kubernetes.io/instance=<app>`; the
Postgres image sets its passwords from the Secret when it starts), then run the deploy again: the
setup Job sets the owner and `bot` passwords. Not tried on a real cluster.

## 7. Differences from the local lab

- **One copy per app**, shared by everyone using the project (section 1).
- **The database owner is not a superuser.** The OpenShift Postgres image makes `lab` the owner of
  the app's database, not a superuser (the setup Job signs in as `postgres` to create the `bot`
  login; that admin login uses the same `db-password`). The lab agent then loads data with the
  tables' triggers left on, which it already handles; a data pack whose tables refer to each other
  in a circle may load differently from the local lab.
- **Bots outside the cluster** reach the app through the Route (HTTPS). The database link (port
  15432, through the agent so conditions apply) is inside the cluster only, unless the variable
  `OCP_EXPOSE_DB` is `true` (by hand: `--expose-db`): then the Service
  `twinlab-<app>-db-external` puts it on a NodePort of every node
  (`oc get svc twinlab-<app>-db-external` shows the port). Bots then use the node's address and
  that port with the `bot` login. Ask the cluster admin first: it opens the port to the network.
- **The portal** runs where you start it and talks to the local labs; it does not manage the labs on
  OpenShift yet.
