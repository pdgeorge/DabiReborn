"""
dabi-chatroom-brain/app.py
--------------------------
Main entry point for dabi-chatroom-brain.
Consumes website.chat.to_dabi from RabbitMQ, routes to LLMService,
publishes response to website.chat.from_dabi.

This is Website Dabi — completely isolated from Stream Dabi.
Separate personality, separate history, separate exchange.

RabbitMQ:
  Inbound:  website_events (fanout), event type: website.chat.to_dabi
            payload: {"author": "...", "text": "..."}
  Outbound: website_events (fanout), event type: website.chat.from_dabi
            payload: {"text": "..."}

.env keys:
  RABBITMQ_URL
  WEBSITE_EXCHANGE    (default: website_events)
  ANTHROPIC_API_KEY
  MOCK_LLM            (default: false)
"""

import asyncio
import json
import logging
import os
import sys

import aio_pika
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "shared"))
from llm_service import LLMService

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dabi-chatroom-brain")

RABBITMQ_URL      = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
WEBSITE_EXCHANGE  = os.getenv("WEBSITE_EXCHANGE", "website_events")
QUEUE_NAME        = "dabi_chatroom_brain"


class Services:
    def __init__(self):
        mock = os.getenv("MOCK_LLM", "false").lower() == "true"
        self.llm = LLMService(
            system_json_path="shared/website_dabi.json",
            mock=mock,
        )


async def main():
    LOGGER.info("dabi-chatroom-brain starting...")

    services = Services()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            WEBSITE_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.bind(exchange)

        LOGGER.info("Connected. Waiting for website chat events...")

        async def handle_message(message: aio_pika.abc.AbstractIncomingMessage):
            async with message.process():
                event_type = message.type or "unknown"

                if event_type != "website.chat.to_dabi":
                    return

                try:
                    payload = json.loads(message.body)
                except json.JSONDecodeError:
                    LOGGER.warning("Invalid JSON in message body")
                    return

                author = payload.get("author", "someone")
                text = payload.get("text", "").strip()

                if not text:
                    return

                LOGGER.info("Website chat from %s: %s", author, text)

                try:
                    prompt = f"{author} says: {text}"
                    response_text = services.llm.chat(prompt)
                except Exception as e:
                    LOGGER.error("LLM error: %s", e)
                    return

                LOGGER.info("Dabi responds: %s", response_text)

                out = aio_pika.Message(
                    body=json.dumps({"text": response_text}).encode(),
                    type="website.chat.from_dabi",
                )
                await exchange.publish(out, routing_key="")
                LOGGER.info("Published website.chat.from_dabi")

        await queue.consume(handle_message)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())