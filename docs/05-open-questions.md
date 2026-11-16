# Open questions

Milestone 1 needs none of these answered. Each is listed with the item it blocks.

## About QTT (before T-23)

1. **What is QTT's stack?** Frontend framework and version, backend language and framework,
   database engine. This decides whether T-05 needs a second stack analyser.
2. **Answered 2026-11-15: no source access.** QTT is built from other evidence
   ([06-evidence.md](06-evidence.md)). New question: **what evidence can we get for QTT?**
   - HAR recordings of each flow: who records them, in which environment (production holds
     customer data; UAT may differ from production), and where are they stored?
   - The UiPath projects themselves, or only descriptions of what they do?
   - Database schema (DDL) for the tables the flows use; any API description?
   - Screenshots or screen recordings of each screen?
3. **Which automations touch QTT today, and will their code be shared?** How many, which are
   UiPath, which already have an agentic or Python version, and who owns each. Where code is not
   shared, someone writes the flow down (`flows/`), and the demo is labelled accordingly.
4. **What else do those automations touch?** If a QTT process reads a key from another application
   first, that application needs a demo copy too, or the automation stays BLOCKED.
5. **Are robot logs for the QTT automations obtainable?** They are the only measured source for
   slowness calibration (T-26). At what logging level, and may the timings (not the values) leave
   the robot host?
6. **Who can confirm a QTT rule?** Every reproduced rule starts as `hypothesis`. Someone has to be
   able to say "yes, that is how QTT behaves".

## About the platform

7. **Where does the monorepo live?** A new GitHub repository, or inside the existing
   `application-twin-studio` one. (Before T-00. Recommendation: new repository; copy the pieces
   being reused.)
8. **Where are the demo apps hosted for sharing?** Any container host works. What is approved, and
   does it allow a hostname per app? (Before T-21.)
9. **Who is the audience for the demo?** Internal engineers can run `docker compose`; a wider
   audience needs the shared host and a login on the portal.
10. **Will several people use one demo app at the same time?** If yes, leases or a copy per person
    move from "not planned" into Milestone 2.
11. **Is bring-your-own data ever real data?** If yes, the masking gate must exist before the first
    such upload, and a data owner has to name the fields. If it is always invented, masking can wait.
12. **What should the showcase app be (T-02)?** A neutral case-management example, or something
    shaped like QTT so that Milestone 1 work carries over more directly.

## Risks

| Risk | Shows up at | What limits it |
|---|---|---|
| The AI-built app looks right but its selectors differ from the real app | T-07, T-25 | Conformance check uses the automations' real selectors; pin the same UI library version |
| The agent reproduces rules wrongly and nobody notices | T-25 | Rules listed in `app.yaml` as `hypothesis`; owner review; trust guard |
| No source access (now the expected case) | E-01 onward | Evidence-driven factory; every item labelled with its basis; HAR recordings are the strongest substitute |
| A HAR or database export with customer data reaches the repository | E-02 | Evidence store outside Git; parser drops credentials and keeps shapes only; a test asserts no recorded value survives into the model |
| A sample or skeleton app is presented as a copy | E-03, T-15 | Mode label on every app and in every session report |
| Recording and bot disagree on a selector | E-02 | Never resolved silently; listed as an open question for a person |
| Demo app scope creeps to "all of QTT" | T-24 | Brief is generated from the demand report; anything else needs a registered automation |
| Slowness numbers are guesses presented as measurements | T-26 | Profile carries its `calibration` source; sessions show injected delay separately |
| The real app changes and the demo drifts | after T-25 | `app.yaml` records the source commit; a periodic check reports how far behind it is |
| One pipeline per app becomes many pipelines to maintain | T-18 onward | One reusable workflow; per-app files only set a path filter |
| A demo passing is read as "the automation is production-ready" | T-27 | Limits stated on the session page and in every write-up |
