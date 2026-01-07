#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "Starting LeveLeledc Scanner..."
echo "Open http://localhost:5000 in your browser"
echo ""
python3 app.py
