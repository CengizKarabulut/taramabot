"""Daily signal performance report entrypoint."""

import asyncio
import html
import sys
import time

from performance_report import generate_performance_report_with_images, report_labels
from telegram_sender import get_telegram_sender


async def generate_daily_report(strategy_name: str | None = None) -> list[str]:
    messages, _ = await generate_daily_report_with_images(strategy_name)
    return messages


async def generate_daily_report_with_images(
    strategy_name: str | None = None,
) -> tuple[list[str], list]:
    return await generate_performance_report_with_images("daily", strategy_name)


if __name__ == "__main__":
    strategy = sys.argv[1] if len(sys.argv) > 1 else None

    async def main():
        messages, image_paths = await generate_daily_report_with_images(strategy)
        _, bot_label = report_labels()
        sender = get_telegram_sender()
        if not image_paths:
            for message in messages:
                sender.send_message(message)
            return
        for index, image_path in enumerate(image_paths):
            caption = f"<b>{html.escape(bot_label)} Gunluk Performans</b>"
            if len(image_paths) > 1:
                caption += f" | Gorsel {index + 1}/{len(image_paths)}"
            sender.send_photo(str(image_path), caption=caption)
            time.sleep(1)

    asyncio.run(main())
