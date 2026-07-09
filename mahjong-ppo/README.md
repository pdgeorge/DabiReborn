# mahjong-ppo

A standalone decision-maker for riichi mahjong, built as practice for writing
game-playing agents in general (this one; a future Mario one; etc). The goal
is to get the `observation -> action` interface right once so that only the
environment adapter changes between games, not the agent-building workflow.

This is **not** one of the Pi Docker services in `ARCHITECTURE.md`. It's a
research component. If/when it's ready to actually play (Tenhou custom lobby,
or a self-hosted table), `LLMService` from `shared/` will sit on top of it to
narrate the moves in Dabi's voice — this package only decides *what* to play.

## Layers

- `engine/` — game-facing types and the mjai protocol adapter. `Observation`
  and `Action` are our own clean dataclasses, deliberately decoupled from raw
  mjai JSON so the policy layer never has to know about wire format.
- `policy/` — the part actually being practiced. `Policy.act(observation) ->
  Action` is the entire contract. Swap implementations without touching
  anything else.
- `bot.py` — mjai-style entrypoint (`bot.py <player_id>`, reads mjai JSON
  lines from stdin) so this can be dropped into `mjai.app` for local
  bot-vs-bot evaluation, or into Akagi/MahjongCopilot-style harnesses.

## Roadmap

1. **Rule-based baseline** (this commit) — `ShantenDiscardPolicy`: always
   discards the tile that minimizes shanten (turns from tenpai), using
   `mahjong` (MahjongRepository) for shanten calculation. No calls (pon/chi/
   kan/riichi) yet — always passes. This exists to prove the adapter/policy
   plumbing end-to-end before any learning is involved.
2. **PPO agent** — wrap `engine/mjai_adapter.py`'s state in a Gymnasium-style
   `reset()`/`step()` env, train a PPO policy (stable-baselines3) via
   self-play against copies of itself / the rule-based baseline. This is the
   actual practice target: the same env-wrapper + PPO-training shape should
   port to a Mario (or any other) agent later with a different adapter.
3. **Calls + riichi/hora legality** — extend `mjai_adapter.py` to expose
   pon/chi/kan/riichi/hora as legal actions (currently stubbed as
   unimplemented, see TODOs), so the learned policy has the full action
   space, not just discards.
4. **Real table** — bridge `bot.py` to a Tenhou custom lobby (documented
   community pattern: bots plus human friends in one private lobby) or a
   self-hosted OpenRiichi/Nama table.

## mjai protocol note

The adapter targets the mjai JSON-lines protocol (tiles as `"1m"`/`"5sr"` for
red five/etc, messages like `start_kyoku`, `tsumo`, `dahai`, `pon`, `chi`,
`kan`, `reach`, `hora`, `ryukyoku`). The parts used by the rule-based baseline
(`start_kyoku`, `tsumo`, `dahai`) are implemented; call-related messages are
stubbed with TODOs and should be verified against a real mjai game log before
being relied on — the exact field names for calls/riichi weren't hand-verified
against a live game log for this initial scaffold.

## Running the baseline sanity check

```
pip install -r requirements.txt
python -m pytest tests/
```
