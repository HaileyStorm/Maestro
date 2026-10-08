"""Prepare reviewed face crops or compose explicitly supplied replacements."""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.h3_face_refine import prepare_crops, compose_crops


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError("Face document exceeds its limit")
    return json.loads(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--observations", required=True)
    prepare.add_argument("--destination", required=True)
    compose = actions.add_parser("compose")
    compose.add_argument("--source", required=True)
    compose.add_argument("--replacement", required=True)
    compose.add_argument("--replacement-sha256", required=True)
    compose.add_argument("--plan", required=True)
    compose.add_argument("--destination", required=True)
    args = parser.parse_args()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    if args.action == "prepare":
        result = prepare_crops(args.source, read_json(args.observations),
                               args.destination, cancel_check=stopped.is_set)
    else:
        result = compose_crops(args.source, args.replacement, read_json(args.plan),
                               args.replacement_sha256, args.destination, cancel_check=stopped.is_set)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
