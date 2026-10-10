"""The Master Sheet: the team's libraries and the production log (docs/M5A-CONTRACT.md
section 2).

Everything here is read-only and built on request from the files that are already the truth:

* ``frameworks``: every channel's framework files (``channel.json`` + the files in the
  shared folder), with size, date and whether their text has been read;
* ``voices``: every channel's voice, with its consent record and voice sample;
* ``music``: every channel's music folder (a folder shared by channels is scanned once),
  with the licence verdict of the edit stage and the track length from ffprobe, cached in
  SQLite ``music_cache`` by path, size and modification time;
* ``production_log``: one row per exported video, from ``08_export/export.json`` of every
  project (archived ones too) plus the ``export.done`` rows of the audit table.

``api/library.py`` serves them; nothing in this package writes to the shared folder or to
a project folder.
"""
