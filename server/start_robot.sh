#!/bin/sh

set -u

server_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$server_dir/startup_led_bootstrap.py"
exec python3 "$server_dir/webServer.py"
