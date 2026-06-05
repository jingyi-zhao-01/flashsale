# Flashsale Release Manifest

`flashsale-release.yaml` is the app-owned release contract for the standalone `flashsale` repository.

It is intentionally narrower than the platform Helm values in `homelab-cloud/charts/flashsales/values.yaml`.

## Ownership Boundary

This manifest owns application intent:

- service replica intent
- autoscaling intent
- request-path and worker runtime knobs
- inventory locking mode
- retry and timeout defaults
- image tag strategy for the app release
- perf profile naming that the app expects

This manifest does **not** own cluster/platform-specific settings:

- namespace
- ingress hosts
- image pull secrets
- external secrets wiring
- node selectors / tolerations / affinity
- cluster-specific resource quotas
- observability credential injection
- Neon / AWS / k3s / Tailscale infrastructure

Those remain in the platform repo.

## Current Phase

Phase 0 keeps `tagStrategy: latest` so the platform can continue its existing deploy flow while the release contract settles down.

Later phases can switch this contract to immutable image tags or per-service image digests.

## Translation Rule

The platform repo should treat this manifest as the source of truth for app-owned fields and merge it into its cluster-specific Helm overlay before deploy.

In other words:

1. `flashsale` declares app intent here.
2. `homelab-cloud` declares environment/platform specifics in Helm values.
3. deploy tooling merges both into the final release.

Today that translation is performed by `homelab-cloud/.github/scripts/render_flashsale_release.py`, which renders a temporary Helm values overlay before `helm upgrade --install`.
