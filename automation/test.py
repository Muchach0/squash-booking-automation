from __future__ import annotations

import argparse
import logging
import sys
import types

TEST_USERS = [
    {
        "email": "your.email@example.com",
        "password": "your-password",
        "booking_windows": [
            {
                "day": "monday",
                "start": "17:00",
                "end": "19:00",
                "moment": "17:00-19:00",
            },
            {
                "day": "sunday",
                "start": "18:00",
                "end": "20:00",
                "moment": "18:00-20:00",
            },
        ],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local live test for reserver_squash using static users."
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium visibly")
    parser.add_argument("--headless", action="store_true", help="Force headless Chromium")
    parser.add_argument("--debug", action="store_true", help="Verbose logs and slower browser")
    parser.add_argument("--date", help="Target date ISO YYYY-MM-DD. Defaults to today + 8 days")
    parser.add_argument(
        "--confirm-booking",
        action="store_true",
        help="Actually confirm bookings. By default this script runs in dry-run mode.",
    )
    return parser.parse_args()


def make_reserver_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        headed=args.headed,
        headless=args.headless,
        dry_run=not args.confirm_booking,
        debug=args.debug,
        date=args.date,
    )


def make_logger(debug: bool) -> logging.Logger:
    logger = logging.getLogger("local_live_booking_test")
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    return logger


if __name__ == "__main__":
    local_args = parse_args()
    logger = make_logger(local_args.debug)
    reserver_args = make_reserver_args(local_args)

    try:
        import reserver_squash
    except ModuleNotFoundError as exc:
        if exc.name == "playwright":
            logger.error(
                "Playwright is required for this live local test. Install the automation dependencies "
                "and browser binaries before running it."
            )
        else:
            logger.error("Missing dependency for live local test: %s", exc.name)
        raise SystemExit(1) from exc

    if reserver_args.dry_run:
        logger.info("Local live test running in DRY RUN mode. No booking confirmation will be submitted.")
    else:
        logger.warning("Local live test will CONFIRM bookings if slots are available.")

    raise SystemExit(
        reserver_squash.main(
            TEST_USERS,
            args=reserver_args,
            logger=logger,
        )
    )
