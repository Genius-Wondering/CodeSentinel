#!/bin/bash
# Quick local setup: creates a virtualenv and installs dependencies.
set -e

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "Setup complete. Activate with: source venv/bin/activate"
echo "Then copy .env.example to .env and set OPENAI_API_KEY before running the app."
