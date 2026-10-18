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