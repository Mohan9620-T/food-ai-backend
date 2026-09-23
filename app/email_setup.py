"""Check provider settings, or explicitly request one delivery test from the CLI."""

import argparse
import json

from app.services.email_service import EmailService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Check settings without sending email")
    action.add_argument("--test-to", help="Explicitly send one test email to this recipient")
    args = parser.parse_args()
    service = EmailService()
    errors = service.configuration_errors()
    print(json.dumps({"provider": service.provider, "configured": not errors, "errors": errors}))
    if errors:
        return 1
    if args.test_to:
        accepted = service.send_text(
            recipient=args.test_to,
            subject="Food AI email delivery test",
            body="This is the email delivery test requested for your Food AI deployment.\n",
        )
        print(
            json.dumps(
                {
                    "provider_accepted": accepted,
                    "inbox_delivery": "Check the recipient inbox and spam folder.",
                }
            )
        )
        return 0 if accepted else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
