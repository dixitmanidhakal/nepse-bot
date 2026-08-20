"""
Database Connection Module

This module handles all database connections and session management.
It uses SQLAlchemy for ORM and provides a clean interface for database operations.

Architecture Benefits:
- Centralized database connection management
- Session lifecycle management
- Connection pooling for performance
- Easy to switch database providers
- Dependency injection support for FastAPI
"""

from typing import Generator
from sqlalchemy import create_engine, text, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import logging

from app.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Create SQLAlchemy engine
# The engine is the starting point for any SQLAlchemy application
# It maintains a pool of connections to the database
engine = create_engine(
    settings.get_database_url(),
    poolclass=QueuePool,  # Connection pooling for better performance
    pool_size=5,  # Number of connections to maintain
    max_overflow=10,  # Maximum number of connections that can be created beyond pool_size
    pool_pre_ping=True,  # Verify connections before using them
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=False,  # Never echo raw SQL — %(param)s patterns break Python's log formatter
)

# Create SessionLocal class
# Each instance of SessionLocal will be a database session
SessionLocal = sessionmaker(
    autocommit=False,  # Don't auto-commit transactions
    autoflush=False,  # Don't auto-flush changes
    bind=engine  # Bind to our engine
)

# Create Base class for declarative models
# All SQLAlchemy models will inherit from this Base class
Base = declarative_base()


# Event listener for connection
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    """
    Event listener that fires when a new database connection is created
    
    This can be used for connection-level configuration like setting timezone,
    enabling extensions, etc.
    
    Args:
        dbapi_conn: The DBAPI connection
        connection_record: The connection record
    """
    logger.debug("New database connection established")


@event.listens_for(engine, "close")
def receive_close(dbapi_conn, connection_record):
    """
    Event listener that fires when a database connection is closed
    
    Args:
        dbapi_conn: The DBAPI connection
        connection_record: The connection record
    """
    logger.debug("Database connection closed")


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function for FastAPI to get database session
    
    This function creates a new database session for each request
    and automatically closes it when the request is complete.
    
    Usage in FastAPI:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    
    Yields:
        Session: SQLAlchemy database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Initialize database
    
    Creates all tables defined in SQLAlchemy models.
    This should be called on application startup.
    
    Note: In production, use Alembic migrations instead of this function.
    """
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise


def test_connection() -> bool:
    """
    Test database connection
    
    Attempts to connect to the database and execute a simple query.
    This is useful for health checks and startup validation.
    
    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        # Create a session
        db = SessionLocal()
        
        # Execute a simple query to test connection
        result = db.execute(text("SELECT 1"))
        
        # Fetch the result
        row = result.fetchone()
        
        # Close the session
        db.close()
        
        # Check if query returned expected result
        if row and row[0] == 1:
            logger.info("✅ Database connection successful!")
            logger.info(f"Connected to: {settings.db_name} at {settings.db_host}:{settings.db_port}")
            return True
        else:
            logger.error("❌ Database connection test failed: Unexpected result")
            return False
            
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        logger.error(f"Connection string: {settings.get_database_url().split('@')[1] if '@' in settings.get_database_url() else 'Invalid URL'}")
        return False


def get_db_info() -> dict:
    """
    Get database information
    
    Returns basic information about the database connection.
    Useful for debugging and monitoring.
    
    Returns:
        dict: Database information including host, port, database name, etc.
    """
    return {
        "host": settings.db_host,
        "port": settings.db_port,
        "database": settings.db_name,
        "user": settings.db_user,
        "pool_size": engine.pool.size(),
        "checked_out_connections": engine.pool.checkedout(),
        "overflow": engine.pool.overflow(),
        "echo": engine.echo,
    }


class DatabaseManager:
    """
    Database Manager Class
    
    Provides high-level database operations and utilities.
    This class can be extended with additional database management functions.
    """
    
    def __init__(self):
        """Initialize database manager"""
        self.engine = engine
        self.SessionLocal = SessionLocal
    
    def create_session(self) -> Session:
        """
        Create a new database session
        
        Returns:
            Session: New SQLAlchemy session
        """
        return self.SessionLocal()
    
    def test_connection(self) -> bool:
        """
        Test database connection
        
        Returns:
            bool: True if connection successful
        """
        return test_connection()
    
    def get_info(self) -> dict:
        """
        Get database information
        
        Returns:
            dict: Database connection information
        """
        return get_db_info()
    
    def close_all_connections(self) -> None:
        """
        Close all database connections
        
        This should be called on application shutdown.
        """
        try:
            self.engine.dispose()
            logger.info("All database connections closed")
        except Exception as e:
            logger.error(f"Error closing database connections: {e}")


# Global database manager instance
db_manager = DatabaseManager()
