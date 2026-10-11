"""Save an actual completed team voice call and staff outcome for demo replay."""
import argparse
import json
import re
from pathlib import Path
from uuid import UUID

from backend.config import Settings
from backend.database.replay import ReplayRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interaction-id", type=UUID, required=True)
    parser.add_argument("--id", required=True, help="Stable recording name")
    parser.add_argument("--category", choices=["routine", "group", "short-notice", "manipulation"], required=True)
    parser.add_argument("--directory", type=Path, default=Path("fixtures/calls"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", args.id):
        parser.error("id must be a short lowercase filename-safe name")
    settings = Settings.from_env()
    try:
        data = ReplayRepository().recording(settings.business_id, args.interaction_id)
        data.update(source="team-recording", id=args.id, category=args.category,
                    original_interaction_id=str(args.interaction_id))
        args.directory.mkdir(parents=True, exist_ok=True)
        path = args.directory / f"{args.id}.json"
        with path.open("x") as output:
            output.write(json.dumps(data, indent=2) + "\n")
        print(f"Exported {path}")
    except (ValueError, FileExistsError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")


if __name__ == "__main__":
    main()
