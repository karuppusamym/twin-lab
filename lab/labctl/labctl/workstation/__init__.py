"""A workstation session: one recording across the applications a person (or a robot) uses.

`labctl.workstation.session.Session` runs the web and desktop recorders in one process, watches
which application is in front, and records each application at the level the session gives it
(`levels`): full for the targets, steps for applications opted in, presence (only that it was
used) for everything else and always for the deny list. At stop it writes one timeline
(`timeline`) and the references a portal may receive (`flow`): applications and filtered step
labels, never a value, a tree or a picture. Design: docs/11-session-recorder.md.
"""
