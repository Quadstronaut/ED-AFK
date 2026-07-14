# Per-account ED boot files — design

**Date:** 2026-07-13
**Status:** approved, implemented

## Problem

Operator has 4 Frontier accounts and wants to launch Elite Dangerous into a
*chosen* account with minimal friction. The repo already wraps MinEdLauncher
(MEL) for account switching, but the account-aware launch lives only in the raw
`ed-autojump launch --commander <name>` CLI — not surfaced anywhere convenient,
and `launch.ps1` deliberately does not launch the game at all (it only flies the
loop after you boot manually).

## Mechanism (confirmed against rfvgyhn/min-ed-launcher docs)

MEL's `/frontier <profile>` flag is the multi-account switch: one game install,
any number of Frontier accounts, each with its own DPAPI-encrypted
`.frontier-<profile>.cred` under `%LOCALAPPDATA%\min-ed-launcher\`. The repo's
`ed_core/config.py` already maps friendly names to slugs:

| Commander    | Slug       |
|--------------|------------|
| CmdrOne      | `account1` |
| CmdrTwo   | `account2` |
| CmdrThree  | `account3` |
| CmdrFour     | `account4` |

MEL install (this machine): `G:\SteamLibrary\steamapps\common\Elite Dangerous\MinEdLauncher.exe`.

## Decisions (from brainstorming Q&A)

- **Scope:** boot the game only — land at the ED main menu, operator takes over.
  No auto-menu-nav, so no per-account menu calibration needed.
- **Surface:** four double-clickable files at the repo root, next to
  `launch.ps1`. No picker. Pick the account by choosing which file to run.
- **Type:** `.cmd` (double-click runs; a `.ps1` would open in an editor).
- **Approach:** direct MEL call, not the `ed-autojump launch` CLI (which spins
  up the venv + runs the full menu-audio-wait/menu-nav flow — heavier than
  "just boot" and nothing here needs it).

## Files

`boot-CmdrOne.cmd`, `boot-CmdrTwo.cmd`, `boot-CmdrThree.cmd`,
`boot-CmdrFour.cmd`. Each:

```bat
"%MEL%" /frontier accountN /edo /autorun /autoquit /skipInstallPrompt
```

- `/edo` launches Odyssey directly (no product prompt); swap to `/edh4` for Horizons.
- `if errorlevel 1 pause` keeps the window open only on failure.
- Guards a missing MEL path with a clear "edit the MEL= line" message.

## Prerequisite (one-time, operator-only)

Only `account1` (CmdrOne) has a `.cred` on disk. Accounts 2–4 must each be
logged in once before their boot file works unattended:

- `.\launch.ps1 setup-frontier-creds` (the repo's tested onboarding wizard), or
- first double-click of a boot file self-onboards: MEL prompts for
  email+password in its window, writes the `.cred`, then every launch after is
  automatic.

Steam must be running (Steam install of ED); MEL handles the Frontier login.

## Out of scope

Auto-starting the AFK jump loop after boot (operator chose boot-only); a picker
menu; per-account menu-nav calibration.
