#!/usr/bin/env bash
# Deploys one install from Cloudflare Workers Builds (deploy command: `bash scripts/deploy-install.sh`).
# wrangler.jsonc stays the id-free template the Deploy button copies, so this splices the install's
# D1 id, KV id and custom domain (build variables KYPP_D1_DATABASE_ID, KYPP_KV_ID, KYPP_DOMAIN) into
# wrangler.prod.jsonc, the way dev:local builds wrangler.local.jsonc, then migrates and deploys.
# keep_vars leaves dashboard vars (KYPP_PUBLIC_URL, KYPP_ROOM_ISSUERS, ...) in place.
set -euo pipefail
cd "$(dirname "$0")/.."
D1_ID=${KYPP_D1_DATABASE_ID:-} KV_ID=${KYPP_KV_ID:-} DOMAIN=${KYPP_DOMAIN:-}
[[ "$D1_ID" =~ ^[0-9a-f-]{36}$ ]] || { echo "KYPP_D1_DATABASE_ID is not a D1 id"; exit 1; }
[[ "$KV_ID" =~ ^[0-9a-f]{32}$ ]] || { echo "KYPP_KV_ID is not a KV namespace id"; exit 1; }
[[ "$DOMAIN" =~ ^[a-z0-9.-]+$ ]] || { echo "KYPP_DOMAIN is not a hostname"; exit 1; }
sed -E \
  -e "s/(\"database_name\": \"kypp\",)/\1 \"database_id\": \"$D1_ID\",/" \
  -e "s/\{ \"binding\": \"OAUTH_KV\" \}/{ \"binding\": \"OAUTH_KV\", \"id\": \"$KV_ID\" }/" \
  -e "s/^(  \"ai\": \{ \"binding\": \"AI\" \},)$/  \"routes\": [{ \"pattern\": \"$DOMAIN\", \"custom_domain\": true }],\n  \"workers_dev\": false,\n  \"preview_urls\": false,\n\1/" \
  wrangler.jsonc > wrangler.prod.jsonc
# Fail loud if wrangler.jsonc changed shape and a substitution silently missed.
for want in "\"database_id\": \"$D1_ID\"" "\"id\": \"$KV_ID\"" "\"pattern\": \"$DOMAIN\"" '"workers_dev": false' '"preview_urls": false'; do
  grep -qF "$want" wrangler.prod.jsonc || { echo "wrangler.prod.jsonc is missing: $want"; exit 1; }
done
npx wrangler d1 migrations apply DB --remote -c wrangler.prod.jsonc
npx wrangler deploy -c wrangler.prod.jsonc | tee /tmp/deploy.log
grep -qF "env.OAUTH_KV ($KV_ID)" /tmp/deploy.log || { echo "deploy did not bind OAUTH_KV to $KV_ID"; exit 1; }
grep -qF "$DOMAIN (custom domain)" /tmp/deploy.log || { echo "deploy did not attach $DOMAIN"; exit 1; }
