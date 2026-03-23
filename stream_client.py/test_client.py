"""
test_client.py
--------------
Publishes a fake Twitch chat message to RabbitMQ and listens
for Dabi's response on the dabi_events exchange.

Usage:
    python test_client.py "Hello Dabi, what's your favourite food?"
"""

import asyncio
import json
import sys
import os

import aio_pika
from dotenv import load_dotenv

load_dotenv()

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
TWITCH_EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "twitch_events")
DABI_EXCHANGE = os.getenv("DABI_EXCHANGE", "dabi_events")


def build_fake_chat_message(text: str) -> dict:
    return {
        "event_type": "channel.chat.message",
        "event_version": "1",
        "event": {
            "broadcaster_user_id": "54654420",
            "broadcaster_user_name": "pdgeorge",
            "chatter_user_id": "99999999",
            "chatter_user_name": "test_user",
            "message": {
                "text": text,
            },
        },
        "metadata": {},
    }


async def main(chat_text: str):
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()

        # Outbound — publish fake Twitch event
        twitch_exchange = await channel.declare_exchange(
            TWITCH_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )

        # Inbound — listen for Dabi's response
        dabi_exchange = await channel.declare_exchange(
            DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        response_queue = await channel.declare_queue("", exclusive=True)
        await response_queue.bind(dabi_exchange)

        # Publish the fake chat message
        payload = build_fake_chat_message(chat_text)
        message = aio_pika.Message(
            body=json.dumps(payload).encode(),
            type="channel.chat.message",
        )
        await twitch_exchange.publish(message, routing_key="")
        print(f"Sent: {chat_text}")
        print("Waiting for Dabi...")

        # Wait for response with a timeout
        async with response_queue.iterator() as queue_iter:
            async for msg in queue_iter:
                async with msg.process():
                    body = json.loads(msg.body)
                    print(f"\nDabi says: {body.get('text', '???')}\n")
                    return


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello Dabi!"
    asyncio.run(main(text))