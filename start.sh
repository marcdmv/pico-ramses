#!/bin/bash
# Launch the RAMSES/R4T web sniffer. Open http://localhost:8765 in Chrome.
cd "$(dirname "$0")"
pkill -f "ramses_tool/server.py" 2>/dev/null   # stop any previous instance
echo "Starting RAMSES tool…  open  http://localhost:8765"
exec python3 server.py
