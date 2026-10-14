# Xingli v2.4.0 — per-MOD updates and Cloudflare R2

This release adds individual MOD update notifications. It does NOT require GitHub Releases and does NOT change the full modpack version.

## Infrastructure

- mods/latest.json (GitHub main, mirrored by Gitee): small, trusted per-MOD catalog.
- Cloudflare R2 public bucket + **custom domain**: large individual ZIP packages.
- Gitee/GitHub raw: manifest mirrors and optional small ZIP mirror.
- Xingli plugin: automatic MO2-startup check (4.4s), prompt even before helper window opens, verified resumable downloads and native MO2 install.

The initial catalog has an empty mods array. No fictional MOD is pushed. A live MOD update is announced only after a real ZIP has been uploaded and listed in the catalog.

## One-time R2 account setup

1. Open https://dash.cloudflare.com/ and create a Standard R2 bucket, e.g. xingli-mod-updates.
2. Connect a public **custom domain** to this bucket, e.g. mods.yourdomain.com. Never use the rate-limited r2.dev test endpoint for production distribution.
3. Create an R2 API token scoped to this bucket (Object Read & Write), and configure a named local AWS CLI profile. Never commit or share secret access keys.
4. Run in your own terminal: aws configure --profile xingli-r2
5. For newer AWS CLI versions, use: aws configure set s3.preferred_transfer_client classic --profile xingli-r2

Official docs: https://developers.cloudflare.com/r2/get-started/cli/ and https://developers.cloudflare.com/r2/buckets/public-buckets/

## Upload an individual MOD

From a checkout of this repository, run this example on one line with your real values:

python development/v2.4.0/publish_mod_to_r2.py --zip "D:/MODPackages/tactical-v1.2.0.zip" --id tactical-armor --mo2-name "现代战术装备" --version 1.2.0 --changelog "修复服装材质" --bucket xingli-mod-updates --r2-account-id <ACCOUNT_ID> --r2-public-base https://mods.yourdomain.com --dry-run

First preview with --dry-run. Once correct, repeat without --dry-run to upload the package. The publisher calculates the actual SHA256 and updates the local mods/latest.json. Confirm that the resulting public URL works before committing and pushing mods/latest.json to GitHub, then ensure Gitee mirrors it.

Important: the ZIP must be a FULL MO2-installable mod package, not a package containing the entire modpack. FOMOD remains handled by the MO2 native installer.

MOD IDs and exact MO2 internal names must remain stable across versions. The official catalog lists R2 custom domains under download_hosts; the plugin accepts author-declared domains, with HTTPS and checksum enforcement.

## Safety and limits

- No MOD replacement when a local version cannot be determined reliably.
- No unverified packages are installed; SHA256 and size are mandatory.
- HTTP Range resume, fallback mirrors, and TLS-only trusted HTTPS hosts.
- Complete backup of the original MO2 MOD before calling organizer.installMod().
- The MO2 native installer handles FOMOD and replacement prompts; this module never silently deletes a player's MOD files, resets priority or forces MO2 to close.
- If native installation fails, the backup is retained for recovery **after MO2 closes**. Automated offline file restoration is not claimed as implemented in this version.
- Do not distribute third-party copyrighted MODs without redistribution permission.
- No R2 credentials, customer data or local secrets in repository or ZIP.

Three independent catalogs: update/latest.json (assistant), modpack/latest.json (full pack), mods/latest.json (individual mods).

## Verification

CI covers the archive syntax and contents, source-manifest invariants, trusted domain validation, resume responses, SHA256 rejection and backup independence. Live R2 throughput, native MO2 FOMOD behavior and recovery require a user machine manual check.

Cloudflare account and R2 bucket are not created by the GitHub build. The owner must authorize/provision them in their Cloudflare dashboard.