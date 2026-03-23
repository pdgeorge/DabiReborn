"""
dabi-stream-brain/app.py
------------------------
Main entry point for dabi-stream-brain.
Consumes Twitch events from RabbitMQ, routes to handlers,
publishes responses to the dabi_events exchange.
"""

import asyncio
import json
import logging
import os
import sys

import aio_pika
from dotenv import load_dotenv

load_dotenv()

# Allow shared/ imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from llm_service import LLMService
from router import route

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dabi-stream-brain")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
TWITCH_EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "twitch_events")
DABI_EXCHANGE = os.getenv("DABI_EXCHANGE", "dabi_events")
QUEUE_NAME = "dabi_stream_brain"


class Services:
    def __init__(self):
        self.llm = LLMService()


async def main():
    LOGGER.info("dabi-stream-brain starting...")

    services = Services()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        # Inbound — Twitch events
        twitch_exchange = await channel.declare_exchange(
            TWITCH_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.bind(twitch_exchange)

        # Outbound — Dabi responses
        dabi_exchange = await channel.declare_exchange(
            DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )

        LOGGER.info("Connected. Waiting for events...")

        async def handle_message(message: aio_pika.abc.AbstractIncomingMessage):
            async with message.process():
                event_type = message.type or "unknown"
                try:
                    payload = json.loads(message.body)
                except json.JSONDecodeError:
                    LOGGER.warning("Invalid JSON in message body")
                    return

                response_text = route(event_type, payload, services)

                if response_text:
                    out = aio_pika.Message(
                        body=json.dumps({"text": response_text}).encode(),
                        type="dabi.tts.ready",
                    )
                    await dabi_exchange.publish(out, routing_key="")
                    LOGGER.info("Published dabi.tts.ready")

        await queue.consume(handle_message)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
