# iStoreOS Packaging Verification

This file tracks the verification status for `plan/26-05-07-iStoreOS_App_Packaging_Plan.md`.

## Local checks

Run from the repository root:

```sh
./openwrt/verify-istoreos-package.sh
git diff --check -- openwrt docs/istoreos.md
```

Current local coverage:

- Planned source files exist under `openwrt/luci-app-wechat-bridge/`.
- Planned app metadata files exist under `openwrt/app-meta-wechat-bridge/`.
- `docs/istoreos.md` and `openwrt/README.md` exist with required sections.
- `docs/istoreos.md` includes concrete API token, `/api/status`, `/api/send`,
  QingLong, `luci-app-wechatpush`, upgrade, and security examples.
- `openwrt/README.md` includes target feed-search commands, strict SDK/app-meta
  verifier examples, Docker dependency injection, and upstream submission order.
- `openwrt/AUDIT.md` records the prompt-to-artifact completion checklist and
  remaining external gates.
- The repository Docker publish workflow targets GHCR and `linux/amd64,linux/arm64`.
- Formal packaging outputs are rejected under `plan/`.
- Temporary/editor junk files are rejected under `openwrt/`.
- Packaging text files are checked for LF line endings.
- Shell scripts pass `sh -n`.
- Runtime entrypoint scripts are executable: `istorec/wechat-bridge.sh`,
  `uci-defaults/99-wechat-bridge`, and app-meta `config.sh`.
- LuCI Lua files are parsed with `luac -p` when `luac` is available; the current
  script prints a skip when no Lua compiler is installed.
- LuCI and app-meta Makefiles include the expected OpenWrt/iStore build helpers.
- App metadata fields match the plan.
- app-meta `config.sh` includes the positive autostart condition for
  `/usr/libexec/istorec/wechat-bridge.sh install`; runtime execution of that
  absolute path still requires the packaged iStore environment.
- The zh-cn LuCI translation file contains the CBI page labels.
- LuCI controller route matches `META_LUCI_ENTRY`.
- LuCI status, port, Web panel link, planned settings fields, static logs, and
  action-button mappings are verifier-backed; buttons save and commit UCI
  before invoking `istorec`.
- When `python3` is available, the verifier checks `/api/status`, `/api/send`
  Bearer token auth, query-token auth, and unauthorized `/api/send` handling
  directly against app handlers without opening a socket.
- Forbidden implementation patterns are absent: `luci-lib-docker`, `zoneinfo-asia`, `lsblk`, anonymous `@main[0]`, and `system.@system[0].timezone`.
- JS LuCI view files and RPC ACL JSON files are absent from the LuCI package.
- Local fake `uci`/`docker` checks cover `uci-defaults`, exact autoconf flags,
  missing `ISTORE_CONF_DIR` for `path`, token generation, preservation of
  existing `config.sh` defaults, status, start/stop/restart/rm command paths,
  port defaults, install failure with empty `config_path`, data directory
  creation, Docker pull/rm/run arguments and env vars, `TZ` fallback,
  missing-container logs, and 100-line logs.
- `logo.png` is a `128x128` PNG and remains below the local 32 KiB metadata
  guardrail.

## Strict SDK check

After confirming the target SDK package names for Docker daemon and CLI:

```sh
REQUIRE_SDK=1 BUILD_IN_SDK=1 \
WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker' \
./openwrt/verify-istoreos-package.sh /path/to/openwrt-sdk
```

Replace `+dockerd +docker` with the actual package names reported by the target SDK if they differ.

This verifies:

- `./scripts/feeds search docker`
- `./scripts/feeds search dockerd`
- `./scripts/feeds search docker-ce`
- `make package/luci-app-wechat-bridge/clean V=s`
- `make package/luci-app-wechat-bridge/compile V=s`

## Upstream app-meta check

As of 2026-05-07, the upstream `linkease/openwrt-app-meta` README documents
that Docker-dependent apps should use `META_DEPENDS:=+docker-deps`.
Public `openwrt-app-meta` change logs also show Docker app Makefiles using
`+docker-deps` for dPanel, CodeServer, Ubuntu, and UptimeKuma.

Before submitting `openwrt/app-meta-wechat-bridge/` to `linkease/openwrt-app-meta`:

```sh
REQUIRE_APP_META=1 \
OPENWRT_APP_META_DIR=/path/to/openwrt-app-meta \
./openwrt/verify-istoreos-package.sh
```

This verifies that the upstream checkout still provides `docker-deps` in `dummy/Makefile`.

## Device checks

These require an iStoreOS/OpenWrt test machine:

- Install the compiled `luci-app-wechat-bridge` IPK.
- Verify iStore autoconfig writes `wechat-bridge.config.config_path` and `enabled=1`.
- Verify container image pull, container creation, and container start.
- Open the Web UI at the configured port.
- Verify `/api/status`.
- Verify API token authentication for protected APIs.
- Verify `status` outputs `not-installed`, `stopped`, and `running` in the corresponding container states.
- Verify `port` outputs the UCI port and defaults to `5200` when empty.
- Verify `TZ` uses `system.@system[0].zonename`, falling back to `UTC`.
- Verify start, stop, restart, upgrade, logs, and uninstall behavior.
- Verify uninstall removes the container and preserves the data directory.
