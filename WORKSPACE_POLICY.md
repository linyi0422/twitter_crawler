# Workspace Policy

## Single Active Output

Only one active output area is allowed:

- `output/deliverables/`

Everything else is either:

- pipeline scratch: `output/_runs/`
- archived history: `output/_archive/`

## Entry Points

Use these files only:

1. `START_HERE.txt`
2. `twitter_crawler/START_HERE.md`
3. `twitter_crawler/output/deliverables/LATEST.txt`
4. `twitter_crawler/output/deliverables/INDEX.md`

## Deliverable Naming

Bundle naming must follow:

- `YYYY-MM-DD_<topic>_<stage>/`

Each bundle must contain:

- `00_README.md`
- one primary result file (for example `10in1_brief.md`)
- `sources/` (or equivalent traceable source material)

## Cleanup Rule

After each run:

1. Move non-deliverable runtime data into `output/_runs/` or `output/_archive/`.
2. Update `LATEST.txt`.
3. Update `INDEX.md`.
4. Never leave loose files directly under `output/` except `.gitkeep`.
