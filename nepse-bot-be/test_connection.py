"""
Database Connection Test Script

This script tests the PostgreSQL database connection.
Run this after setting up your database to verify everything is working.

Usage:
    python test_connection.py
"""

import sys
import logging
from app.database import test_connection, get_db_info
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Main function to test database connection"""
    
    print("\n" + "=" * 60)
    print("NEPSE Trading Bot - Database Connection Test")
    print("=" * 60 + "\n")
    
    # Display configuration
    print("Configuration:")
    print(f"  Database: {settings.db_name}")
    print(f"  Host: {settings.db_host}")
    print(f"  Port: {settings.db_port}")
    print(f"  User: {settings.db_user}")
    print(f"  Environment: {settings.environment}")
    print("\n" + "-" * 60 + "\n")
    
    # Test connection
    print("Testing database connection...")
    success = test_connection()
    
    if success:
        print("\n✅ SUCCESS: Database connection is working!\n")
        
        # Get and display database info
        try:
            info = get_db_info()
            print("Database Information:")
            print(f"  Host: {info['host']}")
            print(f"  Port: {info['port']}")
            print(f"  Database: {info['database']}")
            print(f"  User: {info['user']}")
            print(f"  Pool Size: {info['pool_size']}")
            print(f"  Active Connections: {info['checked_out_connections']}")
            print(f"  Overflow: {info['overflow']}")
        except Exception as e:
            logger.warning(f"Could not get database info: {e}")
        
        print("\n" + "=" * 60)
        print("You can now proceed with the application!")
        print("=" * 60 + "\n")
        
        return 0
    else:
        print("\n❌ FAILED: Could not connect to database!\n")
        print("Troubleshooting steps:")
        print("  1. Make sure PostgreSQL is installed and running")
        print("  2. Check if the database 'nepse_bot' exists")
        print("  3. Verify credentials in .env file")
        print("  4. Check if PostgreSQL is accepting connections")
        print("\nTo create the database, run:")
        print("  psql -U postgres")
        print("  CREATE DATABASE nepse_bot;")
        print("  \\q")
        print("\n" + "=" * 60 + "\n")
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
