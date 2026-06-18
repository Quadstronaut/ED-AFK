# Reusable ED Code — Research Findings (2026-06-18)

> Research workflow (run `wf_3ce5efab-fa5`): 4 source scouts (GitHub, edcodex.info, reddit, Frontier/EDCD)
> → 19 unique candidates → per-candidate **license verification** (AGPL-3.0 compatibility, scout license NOT
> trusted — the real LICENSE file was read) → synthesis. 16 surfaced, 2 dropped.
> Repo is **AGPL-3.0**; operator non-commercial, copyleft fine. Gates: (a) license compat, (b) code public +
> relevant + **not already owned** by ED-AFK.

## TL;DR
The single highest-payoff target is **EDAPGui's nav-panel pipeline** — perspective-warp to de-slant the panel
+ **PaddleOCR with error recovery** — which directly targets ED-AFK's one real gap: the nav-panel reader is
CALIBRATION-PENDING (`navpanel_reader.py` says `DEFAULT_NAV_REGION` is an unvalidated estimate, READ layer
"wired but unproven"). Almost everything else is **study-the-technique, not lift**, because ED-AFK already
owns a typed 40+ event journal parser, WinRT OCR, a pydirectinput scancode sender, and compass/widget/escape
CV. Net shortlist: read **EDAPGui's `EDNavigationPanel.py`** first; the rest is corroboration or out-of-scope.

## Tier 1 — lift / already-in
- **EDAPGui** · MIT · https://github.com/SumZer0-git/EDAPGui — actively maintained (v1.9.2, 801 commits) Python
  autopilot. `EDNavigationPanel.py` = perspective-warp + PaddleOCR destination reader. **REC: study the nav-panel
  pipeline (#1 target); vendor `directinput.py`/sequence snippets only if they beat the existing sender after a read.**
- **EDMCOverlay** · MIT · https://github.com/inorton/EDMCOverlay — DirectX HUD overlay, Python client over TCP 5010.
  **REC: already integrated; keep as protocol/fail-soft reference.**
- **EDAutopilot (skai2)** · MIT · https://github.com/skai2/EDAutopilot — original Python autopilot: DirectInput
  scancodes, HSV + template matching, log-driven docking/refuel state machine. **REC: study the docking/refuel state
  machine; don't inline alpha code. (Canonical ancestor of the EDAutopilot lineage.)**

## Tier 2 — Python, study-the-technique
- **Auto_Neutron** · GPL-3.0 · https://github.com/Numerlor/Auto_Neutron — event-subscription journal parser,
  multi-Status-bit fuel gating. REC: study the gating + subscription; CSV/Qt/AHK skip.
- **Gorfs/FCAutojumper** · GPL-3.0 · https://github.com/Gorfs/FCAutojumper — pydirectinput menu-nav timing/backout
  chains + state-wait loops. REC: study the timing chains; FC logic skip. *(README: Frontier deems it bannable scripting.)*
- **EDCD/EDMarketConnector** · GPLv2-or-later · https://github.com/EDCD/EDMarketConnector — `monitor.py` resumable
  unbuffered streaming journal reader + thorough state tracking. REC: study the resumable reader; extract `monitor.py` with care.
- **EDNeutronAssistant** · MIT · https://github.com/Gobidev/EDNeutronAssistant — journal-path discovery, Spansh call shape.
- **EDAutopilot-v2** · MIT · https://github.com/Matrixchung/EDAutopilot-v2 — thread orchestration (img/IO/script). *(Robigo descendant of skai2.)*
- **DLAcoding/EDAutopilot** · MIT · https://github.com/DLAcoding/EDAutopilot — interdiction/abort safety patterns. *(stale 2022 fork.)*
- **epaga/EliteMiningAssistant** · MIT · https://github.com/epaga/EliteMiningAssistant — pyttsx3 TTS (only if a voice HUD is ever wanted).
- **elite-dangerous-journal-pipeline** · MIT · https://github.com/simonamdev/elite-dangerous-journal-pipeline —
  only the `JournalWatcher` file-monitor is separable; ED-AFK's tail already supersedes it.

## Tier 3 — C#/wrong-domain, schema-reference ONLY (license-clean but NOT Python-portable)
- **EDDiscovery** · Apache-2.0 · https://github.com/EDDiscovery/EDDiscovery — journal schema reference; reimplement, don't port.
- **EDDI** · Apache-2.0 · https://github.com/EDCD/EDDI — event-engine architecture reference.
- **EliteOCR** · GPL-3.0 · https://github.com/seeebek/EliteOCR — OCR preprocessing/calibration/correction loop (commodity screens).
- **Elite-Log-Agent** · MIT · https://github.com/DarkWanderer/Elite-Log-Agent — telemetry submission pattern (archived C#).
- **Journal-Limpet** · LGPL-3.0 · https://github.com/itssimple/journal-limpet — journal event taxonomy cross-check.
- **ExplOCR** · Apache-2.0 · https://github.com/ThoroughlyLostExplorer/ExplOCR — exploration-screen OCR (wrong UI region).

## Dropped (NOT reusable)
- **parselite** (https://github.com/Esvandiary/parselite) — MIT + compatible, but an **empty stub** (3 files, zero `.py`).
- **Marginal/EDMarketConnector** (https://github.com/Marginal/EDMarketConnector) — **GPLv2-ONLY** (no "or later");
  **incompatible** with AGPL-3.0. Use the **EDCD** fork (GPLv2-or-later, compatible) — same codebase, different license posture. Easy to confuse; don't.

## License caveats
- **C#/.NET items (6, Tier 3):** `agpl_compatible: yes` means you may **read and reimplement, NOT copy Python**. Only the
  journal event **schema** is portable — and that's also in Frontier's Player Journal manual + EDCD/EDDN JSON schemas, so marginal value over public docs.
- **GPL/LGPL forward-obligation:** Auto_Neutron, Gorfs, EliteOCR (GPL-3.0), EDCD/EDMC (GPLv2+), Journal-Limpet (LGPL-3.0)
  carry copyleft into ED-AFK's AGPL-3.0 (fine — already AGPL), but any **copied block** must keep its source attribution/notice.
- **MIT items** only require preserving the copyright line.
- **ToS:** input automation is the gray area ED-AFK already operates in; no candidate adds a contractual no-reuse term beyond its OSI license.
