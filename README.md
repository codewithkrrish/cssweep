# cssweep

Find the CSS selectors nothing in your project uses — and clean them
out when you are ready.

## Why I built it

Every site I have shipped has ended up the same way: a stylesheet
full of classes from a layout I dropped months ago, a promo banner
that never launched, a `--brand` token from before a rename. The CSS
kept growing and nobody dared delete anything, because nobody could
prove a selector was dead.

Existing tools either needed a browser and a running site (UnCSS,
PurgeCSS in build mode) or only worked for one framework. I wanted
the simple version: point it at a folder, get a list, delete with
confidence. So I wrote it — no browser, no build step, no config
needed for the common case.

```
$ cssweep ./my-site
styles.css — 7 unused
  line 3: --old-brand  (custom-property)
  line 23: .sidebar  (class)
  line 24: .promo-banner  (class)
  line 24: .tag  (class)
  line 25: .legacy-table  (class)
  line 28: .sidebar  (class)  [in @media (max-width: 600px)]
  line 29: .mobile-only-old  (class)  [in @media (max-width: 600px)]

duplicate selectors (defined more than once):
  .sidebar  — styles.css:23, styles.css:28

7 unused selectors in 17 total across 1 stylesheet(s)
(2 template/script file(s) scanned)
```

Then clean it up:

```
$ cssweep ./my-site --clean --dry-run
would clean ./my-site/styles.css: 5 rules removed, 0 rules trimmed,
1 custom property dropped, ~242 bytes saved
```

## Install

Python 3.10 or newer, no other dependencies.

```bash
pip install git+https://github.com/codewithkrrish/cssweep.git
```

Or clone it and run it from the checkout:

```bash
git clone https://github.com/codewithkrrish/cssweep.git
cd cssweep
pip install -e .
```

## Usage

### Scan a project

```bash
cssweep ./my-site                    # report only, exit 0
cssweep ./my-site --fail-on-unused   # exit 1 if anything is unused (CI)
cssweep ./my-site --format json      # machine-readable report
cssweep ./my-site --verbose          # also list stylesheets that are clean
```

cssweep walks the project for stylesheets (`.css`, `.scss`, `.less`)
and templates/scripts (`.html`, `.js`, `.jsx`, `.ts`, `.tsx`, `.vue`,
`.svelte`, `.astro`, and more), then reports every class, id and
`--custom-property` defined in the CSS but never referenced anywhere
else. It also flags selectors defined more than once.

`node_modules`, `dist`, `build`, `.git` and other generated folders
are skipped automatically.

### Clean up

```bash
cssweep ./my-site --clean --dry-run  # say what would change, change nothing
cssweep ./my-site --clean            # write cleaned copies next to the originals
                                     # (styles.swept.css — originals untouched)
cssweep ./my-site --clean --force    # rewrite the originals in place
```

Cleaning is deliberately careful:

- Without `--force`, the original file is never touched. Cleaned
  output goes to `styles.swept.css` (auto-renamed to
  `styles.swept-2.css` and so on if that file already exists).
- With `--force`, files are rewritten atomically (temp file + rename),
  so a crash mid-write cannot corrupt a stylesheet.
- A rule is only removed when **all** of its selectors are dead. A
  mixed rule (`.card, .dead-card`) keeps the used selector and drops
  the dead one.
- An `@media` block is only removed when every rule inside it is dead.
- Everything kept comes out byte-for-byte identical — cleaning cuts
  text spans, it never re-prints your CSS. Blank lines are left where
  a rule used to be.

### Narrow the scan

```bash
cssweep ./my-site --css "src/**/*.css" --templates "src/**/*.html"
cssweep ./my-site --exclude "vendor/**" --exclude "legacy/**"
cssweep ./my-site --ignore "^js-" --ignore "is-.*"
cssweep ./my-site --skip-ids
```

### Config file

A `.cssweep.json` in the project root is picked up automatically:

```json
{
  "ignore": ["^js-", "is-.*", "no-js"],
  "exclude": ["vendor/**", "legacy/**"],
  "skip_ids": false
}
```

CLI flags add to the config values. `--config` points at a different
file.

## What it checks, and what it does not

| Checks | Does not check |
|---|---|
| `.class` selectors | Classes assembled at runtime (`'btn-' + size`) |
| `#id` selectors (unless `--skip-ids`) | Classes injected by a framework after load |
| `--custom-property` definitions vs `var(--x)` use | `[class~="name"]` attribute selectors |
| Selectors inside `@media` / `@supports` / `@layer` | Nested selectors inside SCSS rule blocks |
| Duplicate selector definitions | Styles inside `<style>` tags are scanned for usage, but a stylesheet is what gets cleaned |

A class counts as used when its exact name appears as a word in any
template or script file — `class="btn"`, `className="btn"`,
`classList.add('btn')`, Vue `:class="{ btn: on }"` all match. That
literal matching is the honest limit of a static tool: if a class is
built from pieces at runtime, cssweep will call it unused. When that
happens, add the name to `ignore` instead of arguing with the tool.

## Try it

There is a small deliberately messy site in `examples/demo-site/`:

```bash
cssweep examples/demo-site
cssweep examples/demo-site --clean --dry-run
```

## Project structure

```
cssweep/
  __init__.py    version
  __main__.py    python -m cssweep entry point
  cli.py         flags, exit codes, wiring (thin layer only)
  scanner.py     finding stylesheets and templates
  cssparse.py    CSS -> rule tree (boundaries, selectors, line numbers)
  selectors.py   class/id/custom-property extraction
  usage.py       what the templates and scripts actually reference
  analyze.py     dead selectors, duplicates
  cleaner.py     span-based rewriting
  report.py      text and JSON output
  config.py      .cssweep.json loading
  errors.py      typed errors, so users never see a traceback
tests/
  test_cssweep.py
examples/
  demo-site/     a small messy site to try it on
```

## Limitations

- SCSS/Less nesting inside a rule block is not analysed: a nested
  `.child` inside `.parent { ... }` is never reported (it is also
  never wrongly removed — cleaning only touches top-level rules).
- The usage check is literal name matching, not a real DOM or JS
  evaluation (see the table above).
- Cleaning leaves blank lines where removed rules were. Run your
  formatter afterwards if that bothers you.

## License

MIT — see [LICENSE](LICENSE).

## Author

Krrish — [github.com/codewithkrrish](https://github.com/codewithkrrish)
