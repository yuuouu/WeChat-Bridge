#!/bin/sh

CONFIG="wechat-bridge"
SECTION="config"
CONTAINER="wechat-bridge"
DEFAULT_PORT="5200"
DEFAULT_IMAGE_NAME="ghcr.io/yuuouu/wechat-bridge"
DEFAULT_IMAGE_TAG="latest"
DEFAULT_MARKDOWN_MODE="markdown"
DEFAULT_WEBHOOK_PORT="18082"

uci_get() {
	uci -q get "${CONFIG}.${SECTION}.${1}" 2>/dev/null
}

load_config() {
	enabled="$(uci_get enabled)"
	port="$(uci_get port)"
	config_path="$(uci_get config_path)"
	image_name="$(uci_get image_name)"
	image_tag="$(uci_get image_tag)"
	api_token="$(uci_get api_token)"
	markdown_mode="$(uci_get markdown_mode)"
	webhook_port="$(uci_get webhook_port)"
	ql_scripts_path="$(uci_get ql_scripts_path)"
	tz="$(uci -q get system.@system[0].zonename 2>/dev/null)"

	[ -n "$port" ] || port="$DEFAULT_PORT"
	[ -n "$image_name" ] || image_name="$DEFAULT_IMAGE_NAME"
	[ -n "$image_tag" ] || image_tag="$DEFAULT_IMAGE_TAG"
	[ -n "$markdown_mode" ] || markdown_mode="$DEFAULT_MARKDOWN_MODE"
	[ -n "$webhook_port" ] || webhook_port="$DEFAULT_WEBHOOK_PORT"
	[ -n "$tz" ] || tz="UTC"
}

docker_container_exists() {
	docker inspect "$CONTAINER" >/dev/null 2>&1
}

do_install() {
	load_config

	if [ -z "$config_path" ]; then
		echo "config_path is empty; configure the data directory first" >&2
		return 1
	fi

	mkdir -p "$config_path" || return 1
	docker pull "${image_name}:${image_tag}" || return 1
	docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

	local ql_vol=""
	if [ -n "$ql_scripts_path" ] && [ -d "$ql_scripts_path" ]; then
		ql_vol="-v ${ql_scripts_path}:/qinglong_scripts:ro"
	fi

	docker run -d \
		--name "$CONTAINER" \
		--restart=unless-stopped \
		-p "${port}:5200" \
		-p "${webhook_port}:18082" \
		-v "${config_path}:/data" \
		$ql_vol \
		-e "PORT=5200" \
		-e "WEBHOOK_LISTEN_PORT=18082" \
		-e "TOKEN_FILE=/data/token.json" \
		-e "DATA_DIR=/data" \
		-e "AI_CONFIG_FILE=/data/ai_config.json" \
		-e "NO_BROWSER=1" \
		-e "API_TOKEN=${api_token}" \
		-e "MARKDOWN_MODE=${markdown_mode}" \
		-e "TZ=${tz}" \
		"${image_name}:${image_tag}"
}

do_start() {
	if docker_container_exists; then
		docker start "$CONTAINER"
	else
		echo "not-installed" >&2
		return 1
	fi
}

do_stop() {
	if docker_container_exists; then
		docker stop "$CONTAINER"
	else
		echo "not-installed"
	fi
}

do_restart() {
	if docker_container_exists; then
		docker restart "$CONTAINER"
	else
		echo "not-installed" >&2
		return 1
	fi
}

do_status() {
	if ! docker_container_exists; then
		echo "not-installed"
		return 0
	fi

	if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]; then
		echo "running"
	else
		echo "stopped"
	fi
}

do_port() {
	port="$(uci_get port)"
	if [ -n "$port" ]; then
		echo "$port"
	else
		echo "$DEFAULT_PORT"
	fi
}

do_logs() {
	if docker_container_exists; then
		docker logs --tail 100 "$CONTAINER" 2>&1 | tail -n 100
	fi
}

do_rm() {
	docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

case "$1" in
	install|upgrade)
		do_install
		;;
	start)
		do_start
		;;
	stop)
		do_stop
		;;
	restart)
		do_restart
		;;
	status)
		do_status
		;;
	port)
		do_port
		;;
	logs)
		do_logs
		;;
	rm)
		do_rm
		;;
	*)
		echo "Usage: $0 {install|upgrade|start|stop|restart|status|port|logs|rm}" >&2
		exit 1
		;;
esac
