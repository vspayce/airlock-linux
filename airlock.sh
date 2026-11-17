#!/bin/sh
export PYTHONPATH=/app/share/airlock${PYTHONPATH:+:$PYTHONPATH}
exec python3 -m airlock "$@"
