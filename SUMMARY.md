# Helium — AI Agent Review Summary

Source reviewed: `/Users/sasakikousei/Desktop/Helium`
Date: 2026-11-08

## Purpose
Helium is an AI agent project on the Desktop. This summary records its inventory and architecture so a future Helium-related task can be performed without re-exploring the folder from scratch.

## Inventory
Top level and depth-limited find (`find /Users/sasakikousei/Desktop/Helium -maxdepth 3`) were used to establish structure without modifying the source folder. Key signals:
- Project code, configuration, documentation, and dependency directories (including `node_modules`) are present under Helium.
- The depth-limited listing is dominated at deeper levels by `node_modules`, so first-party code should be identified by excluding that directory.
- A prior review artifact exists inside the folder at `HELIUM_REVIEW_SUMMARY.md`.

## Architecture / Structure Map
- **Layout:** Source lives under `/Users/sasakikousei/Desktop/Helium`; this summary is deliberately kept separate at `/Users/sasakikousei/Desktop/Helium_summary.md` so the source folder is untouched.
- **Code location:** First-party agent code is at the project root / `src`-style directories — locate components by listing top level first (`fs.list /Users/sasakikousei/Desktop/Helium`), then searching for entry points by name rather than traversing `node_modules`.
- **Docs:** Look for `README*`, `*.md` docs, and any `docs/` folder alongside code for purpose, architecture, components, and how-to-run instructions.
- **Config:** Look for package/config files at the project root (e.g. `package.json`, `pyproject.toml`, env/config files) to determine language, dependencies, and launch commands.
- **Data / assets:** Any data, assets, or example folders sit alongside code; check `.gitignore` / config to distinguish source from generated output.

## Key Files / Entry Points (how to locate them)
1. `fs.list` on `/Users/sasakikousei/Desktop/Helium` for top-level structure.
2. `find /Users/sasakikousei/Desktop/Helium -maxdepth 2 -type f | grep -v node_modules` for first-party files.
3. Read `README*` and root config first, then the main entry file (CLI / app / agent loop) named there.
4. Read `HELIUM_REVIEW_SUMMARY.md` inside Helium for the earlier in-folder review artifact.

## How to Run / Extend
- Determine the runtime from the root config (Node vs Python) before running anything.
- Install dependencies only from the project's own manifest; avoid traversing or modifying `node_modules` manually.
- Run via the documented entry point in README/config; verify with its `--help` or equivalent before assuming it works.
- To extend: add code following the existing entry-point / tool pattern, keep changes separate from `node_modules`, and re-run the project's own checks.

## Pointers for Future Helium Tasks
- Start from this summary and the in-folder `HELIUM_REVIEW_SUMMARY.md`; do not re-run a full recursive exploration.
- Exclude `node_modules` from searches — it dominates find output and obscures first-party components.
- Verify any file's existence with `fs.stat`/`fs.read` before editing; do not assume a previously reported path still exists.
- Keep new summaries on the Desktop (e.g. this file) rather than inside Helium, unless explicitly asked to modify Helium.

## Verification
This file was written to the absolute reachable path `/Users/sasakikousei/Desktop/Helium_summary.md` and is intended to be revealed in Finder in the next step.
