#!/bin/sh
# Start as root only long enough to hand the data volume to the unprivileged stellis user.
set -eu
data="${STELLIS_DATA_DIR:-/data}"
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$data"
    # Volumes created by earlier root-run images hold root-owned files; fix ownership once.
    if [ "$(stat -c %U "$data")" != stellis ]; then
        chown -R stellis:stellis "$data"
    fi
    exec setpriv --reuid=stellis --regid=stellis --init-groups "$@"
fi
exec "$@"
