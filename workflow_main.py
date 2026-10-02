"""Entry point for the Render Workflow service. Registers every task and runs them."""
from tailrace.tasks import app

if __name__ == "__main__":
    app.start()
