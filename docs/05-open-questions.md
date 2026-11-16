# Open questions

Milestone 1 needs none of these answered. Each is listed with the item it blocks.

## About QTT (before T-23)

1. **What is QTT's stack?** Frontend framework and version, backend language and framework,
   database engine. This decides whether T-05 needs a second stack analyser.
2. **May an AI coding agent read QTT's source repository?** Which one (Devin, Copilot, Claude Code),
   under which approval, and may the repository leave the network it lives on? If not, the fallback
   is a brief written by a person from screens, API documentation and DDL.
3. **Which automations touch QTT today?** How many, which are UiPath, which already have an agentic
   or Python version, and who owns each.
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
| Source access is refused | T-24 | Fallback brief from screens, API docs and DDL; slower, lower fidelity |
| Demo app scope creeps to "all of QTT" | T-24 | Brief is generated from the demand report; anything else needs a registered automation |
| Slowness numbers are guesses presented as measurements | T-26 | Profile carries its `calibration` source; sessions show injected delay separately |
| The real app changes and the demo drifts | after T-25 | `app.yaml` records the source commit; a periodic check reports how far behind it is |
| One pipeline per app becomes many pipelines to maintain | T-18 onward | One reusable workflow; per-app files only set a path filter |
| A demo passing is read as "the automation is production-ready" | T-27 | Limits stated on the session page and in every write-up |
