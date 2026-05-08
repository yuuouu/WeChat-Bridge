# OpenWrt / iStoreOS Packaging

本目录包含 WeChat Bridge 的 OpenWrt / iStoreOS 打包源码：

- `luci-app-wechat-bridge/`：LuCI 配置页、UCI 默认配置和 iStore 容器管理脚本。
- `app-meta-wechat-bridge/`：iStore 应用商店元数据和自动配置脚本，后续复制到 `linkease/openwrt-app-meta` 提交。

## Docker 依赖确认

`luci-app-wechat-bridge/Makefile` 不在源码中硬编码 Docker 包名。目标 SDK 确认 Docker daemon 和 CLI 包名后，通过 `WECHAT_BRIDGE_DOCKER_DEPENDS` 注入：

```make
WECHAT_BRIDGE_DOCKER_DEPENDS?=
LUCI_DEPENDS:=+luci-lib-taskd $(WECHAT_BRIDGE_DOCKER_DEPENDS)
```

计划中的候选拆分含义是：

- `dockerd`：Docker daemon / Docker CE Engine。
- `docker`：`/usr/bin/docker` CLI。

该命名与 OpenWrt packages feed 的公开包数据一致：

- `dockerd` package definition: <https://raw.githubusercontent.com/openwrt/packages/master/utils/dockerd/Makefile>
- `docker` package definition: <https://raw.githubusercontent.com/openwrt/packages/master/utils/docker/Makefile>

但 iStoreOS 使用的 feeds 可能与通用 OpenWrt release 不完全一致。提交或发布前必须在目标 SDK 中确认：

```sh
./scripts/feeds update -a
./scripts/feeds search docker
./scripts/feeds search dockerd
./scripts/feeds search docker-ce
```

若目标 SDK 中 daemon 和 CLI 仍分别为 `dockerd` 与 `docker`，编译时使用：

```sh
make package/luci-app-wechat-bridge/compile V=s \
  WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker'
```

若目标 SDK 中 daemon 和 CLI 包名不同，使用该 SDK 的实际包名替换 `WECHAT_BRIDGE_DOCKER_DEPENDS`。

## 编译 luci-app IPK

在 OpenWrt 或 iStoreOS SDK 根目录执行：

```sh
mkdir -p package/wechat-bridge
cp -a /path/to/wechat-bridge/openwrt/luci-app-wechat-bridge package/wechat-bridge/

./scripts/feeds update -a
./scripts/feeds install -a
make defconfig

make package/luci-app-wechat-bridge/clean V=s
make package/luci-app-wechat-bridge/compile V=s \
  WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker'
```

编译产物位于 SDK 的 `bin/packages/*/*/luci-app-wechat-bridge_*.ipk`。

## 测试机安装验证

本仓库提供一个本地检查脚本，可先验证文件清单、shell 语法、app-meta 字段、LuCI 路由、锁定约束和脚本行为：

```sh
./openwrt/verify-istoreos-package.sh
```

完整检查清单见 `openwrt/VERIFICATION.md`。

如果已经解压了目标 OpenWrt/iStoreOS SDK，可以把 SDK 路径传给脚本，它会额外执行 Docker 相关 feeds 查询：

```sh
./openwrt/verify-istoreos-package.sh /path/to/openwrt-sdk
```

发布前建议启用严格模式，避免误把缺少 SDK 的本地检查当作完整验证：

```sh
REQUIRE_SDK=1 \
WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker' \
./openwrt/verify-istoreos-package.sh /path/to/openwrt-sdk
```

如果要让脚本同时把 `luci-app-wechat-bridge` 复制到 SDK 并编译 IPK，增加 `BUILD_IN_SDK=1`：

```sh
REQUIRE_SDK=1 BUILD_IN_SDK=1 \
WECHAT_BRIDGE_DOCKER_DEPENDS='+dockerd +docker' \
./openwrt/verify-istoreos-package.sh /path/to/openwrt-sdk
```

提交 `app-meta-wechat-bridge` 前，还应把 `linkease/openwrt-app-meta` 仓库路径传给脚本，确认上游 `dummy/Makefile` 仍提供 `docker-deps`：

```sh
REQUIRE_APP_META=1 \
OPENWRT_APP_META_DIR=/path/to/openwrt-app-meta \
./openwrt/verify-istoreos-package.sh
```

把 IPK 上传到测试机后执行：

```sh
opkg update
opkg install ./luci-app-wechat-bridge_*.ipk
uci show wechat-bridge
/usr/libexec/istorec/wechat-bridge.sh status
```

如果不是通过 iStore 自动配置安装，需要手动配置数据目录：

```sh
uci set wechat-bridge.config.config_path='/mnt/sda1/istore/WeChatBridge'
uci set wechat-bridge.config.enabled='1'
uci commit wechat-bridge
/usr/libexec/istorec/wechat-bridge.sh install
```

关键验证项：

- `wechat-bridge.config.*` 使用命名 section。
- 首次安装生成非空 `api_token`。
- `status` 在未安装、停止、运行三种状态分别输出 `not-installed`、`stopped`、`running`。
- `port` 输出 UCI 端口，空值时输出 `5200`。
- `docker run` 包含 `--restart=unless-stopped`。
- 容器环境变量包含 `PORT=5200`、`TOKEN_FILE=/data/token.json`、`DATA_DIR=/data`、`AI_CONFIG_FILE=/data/ai_config.json`、`NO_BROWSER=1`、`API_TOKEN`、`MARKDOWN_MODE`、`TZ`。
- `TZ` 读取 `system.@system[0].zonename`，为空时回退 `UTC`。
- 卸载后容器删除，数据目录保留。

## iStore app-meta

将元数据目录复制到 `linkease/openwrt-app-meta`：

```sh
cp -a /path/to/wechat-bridge/openwrt/app-meta-wechat-bridge \
  /path/to/openwrt-app-meta/applications/app-meta-wechat-bridge
```

在 `openwrt-app-meta` 仓库中确认 `dummy/Makefile` 仍提供 `docker-deps`，然后按该仓库流程编译或提交。

`config.sh` 支持 iStore 的 `META_AUTOCONF:=path enable`：

- 仅收到 `path` 时，写入 `wechat-bridge.config.config_path="$ISTORE_CONF_DIR/WeChatBridge"`。
- 仅收到 `enable` 时，写入 `wechat-bridge.config.enabled='1'`。
- 只有 `$ISTORE_DONT_START` 为空且参数包含 `enable` 时，才调用 `/usr/libexec/istorec/wechat-bridge.sh install`。
- 参数必须精确匹配，`enable_path` 和 `path_enable` 不会误触发。

## 上游提交顺序

1. 将编译出的 `luci-app-wechat-bridge` IPK 提交到 `linkease/istore-repo` 的 `pending` 分支。
2. 将 `app-meta-wechat-bridge` 提交到 `linkease/openwrt-app-meta` 的 `main` 分支。
