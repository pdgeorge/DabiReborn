"""
dabi-stream-brain/fake_purchase.py
----------------------------------
Pretends someone bought from Dabi's shop, so you can hear him say thank you
without going live: Twitch only takes channel point redemptions while the
stream is live. It publishes the same dabi.shop.purchase that
twitch-broadcaster's overlay_controller sends after a real sale.

On the Pi, from DabiReborn:
    docker compose exec dabi-stream-brain python fake_purchase.py <name> <item> [copies]

e.g. `... fake_purchase.py pdgeorge shirtA`, or `... pdgeorge tophat 2` for a
second copy.
"""

import asyncio
import json
import os
import sys

import aio_pika

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DABI_EXCHANGE = os.getenv("DABI_EXCHANGE", "dabi_events")


async def main(name: str, item: str, copies: int) -> None:
    purchase = {"user_name": name, "user_login": name.lower(), "item": item, "slot": "", "copies": copies, "cost": 300}
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
        # Expires like the controller's, so it's never said late.
        message = aio_pika.Message(json.dumps(purchase).encode(), type="dabi.shop.purchase", expiration=120)
        await exchange.publish(message, routing_key="")
    print(f"Sent: {name} bought {item} (has {copies}). Dabi should thank them in a few seconds.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1))
