# Gate/Path Walk — operator reference logic (the desired behaviour)

Operator's play-by-play, authored 2026-06-09, **verbatim** (his wording preserved). This is
the *desired* logic — the ground truth the gate walk diffs the **real code** against to
find gaps and erroneous branches. It is intentionally simplified; Operator acknowledges there
are situations it does not cover (those get surfaced interactively). The dynamic
`# script load` routing is Claude's lane to drive.

Diff target: `docs/GATEWALK_CHECKLIST.md` rows reference these sections by name.

---

## # arrival
```
if destination, # destination reached
honk
if fuel low refuel
use ocr > nav to panel star details
supercruise assist around star
if exploration mode, # exploration mode
Once the next target is selected, check compass to see if target system is in front or behind
  if front, orient
  if behind, wait, then orient
jump
```

## # script load
> I acknowledge you must drive the logic on this one. Dynamic is not my specialty, we can
> review all the points here interactively.

(Maps to `_maybe_startup` — Claude drives.)

## # exploration mode
```
use ocr > target planet/moon 1
supercruise assist to target
once target is identified (there should be log output here I believe)
increment number and target next body. Could be next planet or moon of current planet.
when everything is 100% discovered, supercruise escape the last body, orient, jump
```

## # destination reached
```
if destination is the system → supercruise assist the entry star, done executing
if destination is a station or carrier:
    pitch away from the star in any random direction for 4 seconds
    throttle 100% for 7 seconds
    orient towards station/carrier
    supercruise assist to target
if destination is a planetary base → NOT COVERED — Operator must guide step by step, heavy
    vision + ocr (read HUD for ship angle to prevent splat-entries wasting time/integrity)
```

## # docking
```
upon dropping into instance at a station or carrier, simply target said target,
approach to 7.49km or less, then request docking; rest is automatic
```

## # undocking
```
Use ocr, find the AUTO LAUNCH option (usually just 1 down from home position) on the
docked in-ship menu. This takes the ship out with a very high success rate.
Once autodock terminates of its own accord, throttle up 100% until 10.1km away from
station/carrier, orient, jump.
undocking is complete
```

## # new features we need
```
visual recognition of "target obscured" (there ARE indicators) — done interactively
```
