import mariadb

"""
=============================================================================
DATABASE CONFIGURATION - FOR CLI TESTING ONLY (main.py)
=============================================================================

This configuration is used ONLY for testing the system via the command-line
interface (main.py). The main web application (app.py) handles database
connections dynamically through the login form - users enter their credentials
in the browser and connections are established per-session.

To test via CLI:
1. Fill in your database credentials below
2. Run: python main.py

For production use:
- Run: python app.py
- Open http://localhost:5000 in your browser
- Enter database credentials in the login form
=============================================================================
"""

# Example configuration - replace with your actual database credentials for CLI testing
DB_CONFIG = {
    "host": "localhost",        # Database host (e.g., "localhost" or "192.168.1.100")
    "port": 3306,               # Database port (default: 3306)
    "user": "your_username",    # Database username
    "password": "your_password", # Database password
    "database": "your_database"  # Database name
}



def connect_db(user=None, password=None):
    config = DB_CONFIG.copy()
    if user:
        config["user"] = user
    if password:
        config["password"] = password
    return mariadb.connect(**config)