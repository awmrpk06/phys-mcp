"""Check staged file contents without displaying credentials or private material."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def blocked_path(name: str) -> bool:
    path = Path(name)
    return (
        any(part in {".venv", ".ssh", "_temp", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in path.parts)
        or path.name in {".env", ".git-credentials", ".netrc", "id_ed25519", "id_rsa"}
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or path.suffix in {".pem", ".key", ".h5"}
    )


def audit() -> dict:
    environment = dotenv_values(ROOT / ".env", interpolate=False)
    secrets = [
        value.encode() for name, value in environment.items()
        if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", name, re.IGNORECASE)
        and value and len(value) >= 12 and not value.startswith(("YOUR_", "CHANGE_ME"))
    ]
    patterns = [
        re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
        re.compile(rb"gh[pousr]_[0-9A-Za-z_]{20,}"),
        re.compile(rb"github_pat_[0-9A-Za-z_]{20,}"),
        re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    ]
    listing = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    entries = []
    for entry in listing.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            entries.append((metadata.split()[1].decode(), name.decode()))
    findings = []
    total_bytes = 0
    batch = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=ROOT,
        input="".join(f"{object_id}\n" for object_id, _ in entries).encode(),
        capture_output=True, check=True,
    ).stdout
    offset = 0
    for object_id, name in entries:
        end = batch.index(b"\n", offset)
        actual_id, object_kind, size_raw = batch[offset:end].split()
        size = int(size_raw)
        if actual_id.decode() != object_id or object_kind != b"blob":
            raise ValueError("Unexpected Git object in staged file audit")
        payload = batch[end + 1:end + 1 + size]
        offset = end + size + 2
        total_bytes += size
        if blocked_path(name):
            findings.append({"path": name, "reason": "Excluded local/private artifact tracked"})
        if any(secret in payload for secret in secrets) or any(pattern.search(payload) for pattern in patterns):
            findings.append({"path": name, "reason": "Potential credential content; value suppressed"})
        if size >= 95 * 1024 * 1024:
            findings.append({"path": name, "reason": "File exceeds conservative GitHub size budget"})
    return {"staged_files_checked": len(entries), "staged_bytes": total_bytes, "findings": findings, "scope": "Current index only; existing history is not rewritten or certified clean."}


def main() -> None:
    result = audit()
    print(json.dumps(result, indent=2))
    if result["findings"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()