"""mjai-style entrypoint: `python bot.py <player_id>`, reads mjai JSON lines
from stdin, writes response messages to stdout. Lets this policy be dropped
into mjai.app for local bot-vs-bot evaluation, or into an Akagi/
MahjongCopilot-style harness.
"""

import sys

from engine.mjai_adapter import MjaiAdapter
from policy.rule_based import ShantenDiscardPolicy


def main() -> None:
    seat = int(sys.argv[1])
    adapter = MjaiAdapter(seat=seat, policy=ShantenDiscardPolicy())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = adapter.handle_line(line)
        if response is not None:
            print(response, flush=True)


if __name__ == "__main__":
    main()
