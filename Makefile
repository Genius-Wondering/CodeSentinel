# =============================================================================
# CodeSentinel Project Makefile
# =============================================================================
# This file serves as a command shortcut entry point for the project.
# It eliminates the need to remember complex commands.
#
# Usage:
#   make run      - Start the backend server (Uvicorn)
#   make frontend - Start the frontend application (Streamlit)
#   make docker   - Build and start services via Docker Compose
# =============================================================================

.PHONY: run frontend docker


# Start the backend server
run:
	uvicorn app.main:app --reload

# Start the frontend Streamlit application
frontend:
	streamlit run frontend/app.py

# Start the services using Docker Compose
docker:
	docker compose up --build