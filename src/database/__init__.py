from src.config import settings
from src.database.connection import Database

# Process-wide database handle; its lifecycle is driven by the MCP server lifespan in src/server.py.
database = Database(settings)

__all__ = ["Database", "database"]
