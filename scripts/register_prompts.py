"""Script to register application prompts to Langfuse Prompt Management."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    """Register prompt files from prompts/ directory to Langfuse."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        print(
            "[SKIP] Langfuse keys not configured in environment. Skipping prompt registration."
        )
        return

    from langfuse import Langfuse  # pylint: disable=import-outside-toplevel

    langfuse = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    base_dir = Path(__file__).resolve().parent.parent
    prompts_dir = base_dir / "prompts"

    prompts_to_register = [
        {
            "name": "message_router",
            "file": prompts_dir / "router_v1.txt",
            "labels": ["production", "v1"],
            "commit_message": "Router prompt v1",
        },
        {
            "name": "transaction_extractor",
            "file": prompts_dir / "extraction_v1.txt",
            "labels": ["production", "v1"],
            "commit_message": "Extraction prompt v1",
        },
        {
            "name": "store_agent",
            "file": prompts_dir / "agent_v1.txt",
            "labels": ["production", "v1"],
            "commit_message": "Store agent prompt v1",
        },
    ]

    for item in prompts_to_register:
        path = item["file"]
        if not path.exists():
            print(f"[WARN] Prompt file {path} not found.")
            continue

        content = path.read_text(encoding="utf-8")
        print(f"Registering prompt '{item['name']}' ({path.name}) to Langfuse...")
        try:
            created = langfuse.create_prompt(
                name=item["name"],
                prompt=content,
                labels=item["labels"],
                commit_message=item["commit_message"],
            )
            print(
                f"  [OK] Successfully registered '{created.name}' version {created.version}"
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"  [ERROR] Failed to register '{item['name']}': {exc}")

    langfuse.flush()
    print("Done registering prompts.")


if __name__ == "__main__":
    main()
