# Job icon assets

Standard FFXIV Companion job icons, supplied by the XIVAPI Class/Job Icons
repository. Original artwork is FINAL FANTASY XIV artwork by Square Enix.

Source: https://github.com/xivapi/classjob-icons/tree/766cb47831435a83f04d904146bd3472501564c5/companion

The original PNGs are bundled unchanged. Filenames use lowercase job
abbreviations. Upstream filenames are lowercase job names without spaces,
except PCT (`pct.png`) and VPR (`vpr.png`).

These files are uploaded as application-owned Discord emojis named `job_sam`,
`job_drk`, etc. They do not consume any server's custom emoji slots.

When adding a future job in `app/domain/jobs.py`, add its icon here as
`abbreviation.png`. Jobs without an available icon retain plain text labels.
