# ed-trading

Trading domain for the ED-AFK bot. Phase 1: an empty scaffold that registers
nothing yet. Unlike solo combat, ed-trading is auto-discovered: it declares a
`trading = "ed_trading:activate"` entry-point in the `ed_autojump.plugins`
group (explore-style), so the CLI plug-in loop co-activates it additively the
moment it is installed. Its `activate()` is idempotent and a no-op in Phase 1.
Real build is Phase 2. Depends on ed-core + ed-vision; never imports a sibling
domain.

Part of the ED-AFK workspace. See the repo root for the full project.

## Pit-stop note (OUT OF SCOPE — NOT implemented)

Reserved future concept, no code: a mid-route docking pit-stop that SELLS
high-value cargo at an intermediate station before the final destination, then
RE-BUYS to refill, as an optimization for multi-condition trade trips
(sell-high-then-rebuy). This is a NOTE only. Phase 1 implements none of it and
reserves no executable code, no steps, and no procedures for it.
