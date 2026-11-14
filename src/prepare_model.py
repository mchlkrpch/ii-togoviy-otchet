"""Download the public encoder once. Runtime inference is strictly offline."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


REVISION = "e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt")
DEFAULT_PATH = Path(__file__).resolve().parent / ".models" / "rubert-tiny2"


def prepare(destination=DEFAULT_PATH):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in FILES:
        target = destination / name
        if not target.exists():
            url = f"https://huggingface.co/cointegrated/rubert-tiny2/resolve/{REVISION}/{name}"
            print(f"Downloading {name}", flush=True)
            temporary = target.with_suffix(target.suffix + ".download")
            try:
                with urllib.request.urlopen(url, timeout=180) as response, temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps({"model": "cointegrated/rubert-tiny2",
        "revision": REVISION, "sha256": hashes}, indent=2) + "\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    print(prepare(parser.parse_args().output))
