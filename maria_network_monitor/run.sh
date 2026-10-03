#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Maria Network Monitor..."

exec /opt/maria-venv/bin/python /app/maria_monitor.py
