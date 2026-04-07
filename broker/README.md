# Broker service foundation

This directory establishes the managed-trial broker as a separate deployable service in the monorepo.

## Explicit rollout boundary

- Runtime stack: TypeScript + Hono on Cloudflare Workers with native D1 and Worker secrets.
- Hosting scope: single-region rollout assumption for the initial Worker deployment, with D1 `location_hint` set to `apac`.
- Managed free-trial path: `OpenRouter` + `google/gemma-4-26b-a4b-it`.
- Inference boundary: the app talks to OpenRouter directly; the broker remains a trial and credential broker.
- Out of scope in this foundation: translation proxying, multi-region deployment, KV, R2, and admin dashboard work.

## Deploy note

`broker/wrangler.jsonc` intentionally uses a non-secret placeholder `database_id`. A real Cloudflare D1 identifier must be supplied in deployment-specific configuration before the service is deployed.

Use `pnpm --filter @puripuly-heart/broker run verify:config` to exercise the pinned Wrangler CLI against `broker/wrangler.jsonc` without requiring cloud credentials.

## Verification environment

Broker verification is Linux-only. Run `pnpm install`, Vitest, and Wrangler from a Linux-native workspace (for example, a WSL-internal path or a regular Linux checkout), not from Windows or shared `/mnt/c/...` `node_modules`.
