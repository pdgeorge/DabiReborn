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
TWITCH_QUEUE_NAME = "dabi_stream_brain"
DABI_QUEUE_NAME = "dabi_stream_brain_inbound"


class Services:
    def __init__(self):
        mock = os.getenv("MOCK_LLM", "false").lower() == "true"
        self.llm = LLMService(mock=mock)


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
        twitch_queue = await channel.declare_queue(TWITCH_QUEUE_NAME, durable=True)
        await twitch_queue.bind(twitch_exchange)

        # Inbound — Dabi events (discord messages coming back in)
        dabi_exchange = await channel.declare_exchange(
            DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        dabi_queue = await channel.declare_queue(DABI_QUEUE_NAME, durable=True)
        await dabi_queue.bind(dabi_exchange)

        LOGGER.info("Connected. Waiting for events...")

        async def handle_message(message: aio_pika.abc.AbstractIncomingMessage):
            async with message.process():
                event_type = message.type or "unknown"
                try:
                    payload = json.loads(message.body)
                except json.JSONDecodeError:
                    LOGGER.warning("Invalid JSON in message body")
                    return

                response_text, response_event_type = route(event_type, payload, services)

                if response_text and response_event_type:
                    out = aio_pika.Message(
                        body=json.dumps({"text": response_text}).encode(),
                        type=response_event_type,
                    )
                    await dabi_exchange.publish(out, routing_key="")
                    LOGGER.info("Published %s", response_event_type)

        await twitch_queue.consume(handle_message)
        await dabi_queue.consume(handle_message)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())