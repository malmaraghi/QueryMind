"""
=============================================================================
SCHEMA LOADER - FOR CLI TESTING ONLY (main.py)
=============================================================================

This module is used ONLY for the CLI testing interface (main.py).
The main web application (app.py) loads schemas dynamically from the
database connection established through the user's login session.

See db_config.py for database configuration.
=============================================================================
"""

import mysql.connector as mariadb

from db_config import DB_CONFIG


def load_schema():
    try:
        conn = mariadb.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = [row[0] for row in cursor.fetchall()]

        schema_text = ""
        for table in tables:
            cursor.execute(f"SHOW CREATE TABLE `{table}`;")
            result = cursor.fetchone()
            if result and len(result) > 1:
                schema_text += f"{result[1]};\n\n"
        
        cursor.close()
        conn.close()
        return schema_text
    except Exception as error:
        return f"Could not read schema from database: {error}"


def get_accessible_tables():
    try:
        conn = mariadb.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return tables
    except Exception as error:
        return []