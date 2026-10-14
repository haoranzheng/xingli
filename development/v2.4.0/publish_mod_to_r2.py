#!/usr/bin/env python3
# coding: utf-8
"""Author-side MOD update publisher for Cloudflare R2 + GitHub/Gitee catalog.

No credentials are written into this repository. The script uploads only the
selected ZIP to R2, calculates SHA256 locally, and updates mods/latest.json.
The author reviews and commits that JSON separately. GitHub Releases unused.

Prerequisites: AWS CLI configured with R2-specific credentials:
  aws configure --profile xingli-r2
  aws configure set s3.preferred_transfer_client classic --profile xingli-r2
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "mods/latest.json"
VALID_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VALID_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,4}$")
VALID_HASH = re.compile(r"^[0-9a-f]{64}$")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            sha.update(chunk)
    return sha.hexdigest()


def public_base(raw):
    parsed = urlsplit(raw.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("R2 public base must be an HTTPS custom domain")
    host = parsed.hostname.lower()
    if (host.endswith(".r2.dev") or host == "localhost" or
            host.endswith((".local", ".internal")) or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("use an R2 custom domain, not an r2.dev test link")
    return "https://" + host, host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path, help="Full MOD ZIP")
    parser.add_argument("--id", required=True, help="stable MOD slug e.g. armor-update")
    parser.add_argument("--mo2-name", required=True, help="exact installed MO2 folder name")
    parser.add_argument("--version", required=True, help="e.g. 1.2.0")
    parser.add_argument("--changelog", required=True)
    parser.add_argument("--bucket", required=True, help="R2 bucket name")
    parser.add_argument("--r2-public-base", required=True,
                        help="https://mods.yourdomain.example")
    parser.add_argument("--r2-account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                        help="Cloudflare account ID (never the API secret)")
    parser.add_argument("--aws-profile", default="xingli-r2")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate, preview manifest, but do not upload or edit it")
    args = parser.parse_args()
    if not VALID_SLUG.fullmatch(args.id):
        parser.error("invalid MOD id; use lowercase letters/numbers/hyphens")
    if not VALID_VERSION.fullmatch(args.version):
        parser.error("version must be like 1.2.0")
    if (not args.mo2_name or len(args.mo2_name) > 180 or
            any(x in args.mo2_name for x in ("/", "\\", "\0"))):
        parser.error("invalid MO2 installed name")
    if not args.zip.is_file() or not args.zip.suffix.lower() == ".zip":
        parser.error("--zip must be an existing .zip file")
    if args.zip.stat().st_size > 8 * 1024 ** 3:
        parser.error("package exceeds 8 GB per-MOD limit")
    if not args.bucket or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,61}[a-z0-9]", args.bucket):
        parser.error("invalid R2 bucket name")
    if not args.r2_account_id or not re.fullmatch(r"[a-f0-9]{32}", args.r2_account_id, re.I):
        parser.error("invalid R2 account id")
    base, host = public_base(args.r2_public_base)
    sha = digest(args.zip)
    key = "mods/%s/%s.zip" % (args.id, args.version)
    url = base + "/" + key
    entry = {
        "id": args.id,
        "mo2_name": args.mo2_name,
        "version": args.version,
        "changelog": args.changelog,
        "package": {
            "size": args.zip.stat().st_size,
            "sha256": sha,
            "mirrors": [{"name": "Cloudflare R2", "url": url}],
        },
    }
    data = json.loads(CATALOG.read_text(encoding="utf-8-sig"))
    if data.get("product") != "xingli_mod_updates" or data.get("pack_id") != "xingli-skyrim":
        raise SystemExit("Catalog product/pack mismatch")
    data.setdefault("download_hosts", [])
    if host not in data["download_hosts"]:
        data["download_hosts"].append(host)
    data["mods"] = [m for m in data["mods"] if m.get("id") != args.id] + [entry]
    data["mods"].sort(key=lambda m: m["id"])
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("MOD:", args.mo2_name, "release:", args.version)
    print("R2 URL:", url)
    print("SIZE:", entry["package"]["size"])
    print("SHA256:", sha)
    if args.dry_run:
        print("DRY RUN: upload and catalog modifications skipped")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        return

    # Keep the cloud password out of command arguments, stdout and git history.
    # The named AWS CLI profile holds limited bucket-scoped S3 credentials.
    endpoint = "https://%s.r2.cloudflarestorage.com" % args.r2_account_id
    subprocess.run([
        "aws", "--profile", args.aws_profile,
        "--endpoint-url", endpoint,
        "s3", "cp", str(args.zip.resolve()),
        "s3://%s/%s" % (args.bucket, key),
        "--content-type", "application/zip",
    ], check=True)
    # A successful CLI exit is necessary but not sufficient: the author must
    # verify the public URL and commit the JSON before notifying players.
    temporary = CATALOG.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(CATALOG))
    print("R2 upload completed; local mods/latest.json generated.")
    print("Verify the public URL and publish the JSON to main/Gitee mirror.")


if __name__ == "__main__":
    main()
