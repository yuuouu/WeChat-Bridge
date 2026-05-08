# iStoreOS Packaging Completion Audit

Objective: execute `plan/26-05-07-iStoreOS_App_Packaging_Plan.md` by adding a
Docker-managed iStoreOS/OpenWrt packaging layer for WeChat Bridge, with LuCI
configuration, iStore metadata, user/build documentation, and the planned
verification coverage.

## Completion status

Not complete. The local implementation is present and locally verified, but the
plan still requires external evidence from a target SDK, upstream app-meta
checkout, Lua compiler, and iStoreOS/OpenWrt runtime device.

## Prompt-to-artifact checklist

| Plan requirement | Evidence | Status |
| --- | --- | --- |
| Do not place formal outputs under `plan/` | Packaging artifacts are under `openwrt/`; user docs are under `docs/istoreos.md`. | Satisfied |
| Add `openwrt/luci-app-wechat-bridge/` | Directory exists with planned files. Verified by `verify-istoreos-package.sh`. | Satisfied |
| Add `openwrt/app-meta-wechat-bridge/` | Directory exists with `Makefile`, `config.sh`, and `logo.png`. Verified by `verify-istoreos-package.sh`. | Satisfied |
| Add iStoreOS user docs | `docs/istoreos.md` covers install, first Web UI use, API token, `/api/status`, `/api/send`, QingLong, OpenWrt notification, management, uninstall, and security; verifier checks key examples. | Satisfied |
| App API surface used by docs | Verifier directly exercises `/api/status`, `/api/send` Bearer auth, query-token auth, and unauthorized `/api/send` against app handlers when `python3` is available. | Satisfied locally |
| Add SDK build docs | `openwrt/README.md` covers Docker dependency confirmation, feed-search commands, strict verifier examples, IPK build, test-machine validation, app-meta submission, and upstream order; verifier checks key commands. | Satisfied |
| Docker-managed runtime, not native Python OpenWrt package | `istorec/wechat-bridge.sh` manages Docker pull/run/start/stop/restart; no Python OpenWrt packaging files were added. | Satisfied |
| GHCR multi-arch image source | `.github/workflows/docker-publish.yml` builds and pushes `${{ github.repository }}` to `ghcr.io` for `linux/amd64,linux/arm64`, matching `ghcr.io/yuuouu/wechat-bridge`; live registry availability still requires registry/device validation. | Partially verified |
| Runtime script entrypoints executable | Verifier checks executable bits for `istorec/wechat-bridge.sh`, `uci-defaults/99-wechat-bridge`, and app-meta `config.sh`. | Satisfied |
| First release arch: `x86_64 aarch64` | `openwrt/app-meta-wechat-bridge/Makefile` has `META_ARCH:=x86_64 aarch64`. | Satisfied |
| Image defaults | UCI defaults and scripts use `ghcr.io/yuuouu/wechat-bridge:latest`. | Satisfied |
| Container name `wechat-bridge` | `istorec/wechat-bridge.sh` sets `CONTAINER="wechat-bridge"`. | Satisfied |
| Default Web port `5200` | UCI default, `config.sh`, and `istorec` fallback use `5200`. | Satisfied |
| Default iStore data path | `config.sh path` writes `$ISTORE_CONF_DIR/WeChatBridge`; verified by fake UCI check. | Satisfied |
| Generate 32-hex API token when empty | `uci-defaults` and `config.sh` generate tokens; verifier checks token shape. | Satisfied |
| LuCI package file list | All planned LuCI package files are present; verifier requires each file. | Satisfied |
| Named UCI section and paths | `root/etc/config/wechat-bridge` uses `config wechat-bridge 'config'`; scripts use `wechat-bridge.config.*`. | Satisfied |
| `Makefile` uses LuCI build include | `luci-app-wechat-bridge/Makefile` includes `$(TOPDIR)/feeds/luci/luci.mk`. | Satisfied |
| Makefiles include build helpers | Verifier checks LuCI and app-meta `$(TOPDIR)/rules.mk`, plus app-meta `../../meta.mk`. | Satisfied |
| `LUCI_PKGARCH:=all` | Present in LuCI package `Makefile`. | Satisfied |
| `LUCI_DEPENDS` includes `+luci-lib-taskd` | Present in LuCI package `Makefile`. | Satisfied |
| Do not hardcode unconfirmed Docker dependencies | LuCI package uses `WECHAT_BRIDGE_DOCKER_DEPENDS?=`; README documents candidate package definitions and target SDK confirmation. | Satisfied locally; target SDK confirmation blocked |
| Exclude `luci-lib-docker`, `zoneinfo-asia`, `lsblk` | Verifier rejects these patterns under packaging dirs. | Satisfied |
| Package uninstall removes container and preserves data | LuCI package `prerm` calls `istorec ... rm`; `rm` only removes Docker container. Device uninstall still unverified. | Partially verified |
| `uci-defaults` scope | Creates named section, sets generic defaults, generates token, does not write `config_path`, and does not start container; verifier checks this. | Satisfied |
| LuCI page status, port, Web panel link | CBI page calls `status` and `port` and renders an `Open Web Panel` link; verifier checks the wiring. | Satisfied locally; device rendering unverified |
| LuCI page settings | CBI page exposes enable, port, data directory, image name, image tag, API token, and markdown mode; verifier checks fields, defaults, port datatype, token password masking, and markdown choices. | Satisfied |
| LuCI page actions | CBI buttons queue install/rebuild, start, stop, restart, and upgrade; `on_after_save` commits UCI before action; verifier checks each mapping. | Satisfied |
| LuCI log area | CBI `TextValue` calls `istorec ... logs`; script caps logs at 100 lines; verifier checks the static log widget wiring. | Satisfied locally; LuCI rendering unverified |
| zh-cn LuCI translations | `po/zh-cn/wechat-bridge.po` includes the CBI labels; verifier checks key `msgid` entries. | Satisfied |
| Do not replicate app Web UI features in LuCI | CBI page only exposes package/container configuration and logs. | Satisfied |
| No JS LuCI page or RPC ACL JSON | Package uses Lua CBI under `luasrc/model/cbi`; verifier rejects JS LuCI view files and JSON under the LuCI package. | Satisfied |
| Controller route | Controller registers `admin/services/wechat-bridge` with title `WeChat Bridge` and `cbi("wechat-bridge")`. | Satisfied |
| Route matches app-meta `META_LUCI_ENTRY` | Verifier checks controller route and app-meta entry. | Satisfied |
| `istorec` command surface | Script supports `install`, `upgrade`, `start`, `stop`, `restart`, `status`, `port`, `logs`, and `rm`; verifier checks command patterns. | Satisfied |
| `install` and `upgrade` share `do_install()` | Case branch maps `install|upgrade` to `do_install`. | Satisfied |
| `do_install()` requires non-empty `config_path` only | Script checks `config_path` only inside `do_install`; verifier checks empty-path install failure and other commands. | Satisfied locally |
| `docker pull`, old container removal, new container creation | `do_install` performs pull, `docker rm -f`, and `docker run`; verifier checks the fake Docker log. | Satisfied locally |
| `docker run` restart, port, mount, env vars | Script includes `--restart=unless-stopped`, `${port}:5200`, `${config_path}:/data`, and required env vars; verifier checks the concrete fake Docker arguments. | Satisfied locally |
| `TZ` uses `system.@system[0].zonename`, fallback `UTC` | Script reads `zonename`; verifier checks Asia/Shanghai and UTC fallback; forbidden `timezone` pattern rejected. | Satisfied locally |
| `status` outputs three single-line states | Verifier checks `not-installed`, `stopped`, and `running`. | Satisfied locally |
| `port` reads UCI and falls back to `5200` | Verifier checks UCI value and empty fallback. | Satisfied locally |
| `logs` handles missing container and caps output | Verifier checks empty missing-container logs and 100-line cap. | Satisfied locally |
| `start`/`stop`/`restart`/`rm` command paths | Verifier checks missing-container start/restart failures, missing-container stop output, and Docker calls for start, stop, restart, and rm. | Satisfied locally |
| `rm` preserves data | Script ignores Docker removal failure and does not touch `config_path`. Device uninstall still unverified. | Partially verified |
| app-meta file list and logo | Planned files present; verifier checks `logo.png` as 128x128 PNG and below 32 KiB. | Satisfied |
| app-meta field values | Verifier checks all planned metadata fields and version sync with `app/version.py`. | Satisfied |
| `docker-deps` upstream prerequisite | Upstream README documents `META_DEPENDS:=+docker-deps` for Docker apps as of 2026-05-07, and public `openwrt-app-meta` change logs show Docker app Makefiles using `+docker-deps` for dPanel, CodeServer, Ubuntu, and UptimeKuma; strict `dummy/Makefile` checkout check remains pending. | Partially verified |
| `config.sh` exact flag scan | Script uses per-argument `case`; verifier checks `path enable`, path-only, enable-only, `enable_path`/`path_enable`, and missing `ISTORE_CONF_DIR` for `path`. | Satisfied |
| `config.sh` named section/default/token behavior | Script ensures `wechat-bridge.config`, sets only missing defaults, generates token, and commits UCI; verifier checks these behaviors. | Satisfied |
| `ISTORE_DONT_START` behavior | Verifier runs `config.sh path enable` with `ISTORE_DONT_START=1`; the run succeeds locally while writing UCI only. It also statically checks the positive autostart condition and absolute `istorec install` call; executing that absolute path remains part of device validation. | Partially verified |
| Shell syntax checks | `verify-istoreos-package.sh` runs `sh -n` on shell scripts. | Satisfied |
| Lua syntax checks | Verifier runs `luac -p` when `luac` exists. Current machine has no `luac`, so this is skipped. | Blocked |
| Target SDK Docker package confirmation | Verifier can run `scripts/feeds search docker/dockerd/docker-ce` when an SDK path is provided. No SDK is available locally. | Blocked |
| SDK IPK compile | Verifier can copy and compile package when `BUILD_IN_SDK=1` and SDK path are provided. No SDK is available locally. | Blocked |
| iStoreOS device runtime tests | Device checklist is recorded in `openwrt/VERIFICATION.md`; no test device is available locally. | Blocked |

## Commands last run

```sh
./openwrt/verify-istoreos-package.sh
git diff --check -- openwrt docs/istoreos.md
```

Both commands pass local checks. The verifier explicitly reports skips for
missing `luac`, missing SDK path, and missing upstream app-meta checkout.

## Required next inputs

To complete the remaining gates, provide one or more of:

- A target OpenWrt/iStoreOS SDK path, then run:
  `REQUIRE_SDK=1 BUILD_IN_SDK=1 WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker' ./openwrt/verify-istoreos-package.sh /path/to/openwrt-sdk`
- A local `linkease/openwrt-app-meta` checkout path, then run:
  `REQUIRE_APP_META=1 OPENWRT_APP_META_DIR=/path/to/openwrt-app-meta ./openwrt/verify-istoreos-package.sh`
- A Lua compiler on `PATH`, then rerun `./openwrt/verify-istoreos-package.sh`
- Access to an iStoreOS/OpenWrt test machine for the runtime checks listed in
  `openwrt/VERIFICATION.md`

Direct shell network fetches are unavailable in this environment:
`git ls-remote https://github.com/linkease/openwrt-app-meta.git HEAD` fails
with `Could not resolve host: github.com`.
