# ED-AFK

AI-assisted AFK automation tools for Elite Dangerous: Odyssey. Personal-learning
project — public so anyone can read, fork, and learn from how we built it.

> Frontier does not currently ban for AFK exploration / honking automation —
> the project owner has run similar automation on fleet carriers and streamed
> it. We use only public files (Player Journal, Status.json) and synthesized
> keyboard input via DirectInput scancodes. Use at your own risk; if Frontier's
> stance changes, the responsibility is yours.

## Project layout

```
ED-AFK/
├── README.md              <- you are here
├── LICENSE
├── .gitignore
├── docs/
│   └── shared/            <- shared reference material (journal events, FSD
│                             constants, star classes, etc.) usable by every
│                             tool in this repo
└── projects/
    └── ed-autojump/       <- first tool: autonomous explorer (jump, scoop,
        ├── SPEC.md           honk, FSS, DSS, danger-class refusal)
        └── src/           <- TBD on first implementation pass
```

The repo is structured as a monorepo because most of the tooling (journal
parser, key sender, Status watcher, route math) is shared.

## Tools (planned)

| Tool | Purpose | Status |
|---|---|---|
| **ed-autojump** | Autonomous exploration: jump, scoop, honk, FSS, DSS, with danger-class refusal and fuel-safe routing | spec draft |
| _future_ | Open to ideas — mining, Robigo, cargo missions, exobiology | — |

## Why this matters

The well-known ED automation projects (skai2/EDAutopilot, Auto_Neutron) work
but they're aging, fragile to lighting/HDR, and built before the journal got
its richest exploration events. The bet here is that a journal-driven core
with computer vision only where journal data is silent (FSS / DSS / cockpit
nav bobble during docking) gives better robustness for less code.

## Quickstart (when implementation exists)

TBD. Will require:
- Windows 10/11
- Python 3.11+
- Elite Dangerous Odyssey
- A keyboard bind preset imported from `projects/ed-autojump/binds/ED-AFK.4.2.binds`
- Optional but recommended: [EDHM-UI-V3](https://github.com/BlueMystical/EDHM_UI) with
  the project's recommended high-contrast palette installed for CV-heavy modes

## License

See [LICENSE](./LICENSE). MIT, with the caveat that if we incorporate code from
GPL'd projects (notably EDMC, EDDI) we will relicense before merging that code.

## Contributing

This is a personal project but PRs and issues are welcome. The spec for the
first tool is at [projects/ed-autojump/SPEC.md](./projects/ed-autojump/SPEC.md).
