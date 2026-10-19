import mariadb

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "your_username",
    "password": "your_password",
    "database": "your_database"
}



def connect_db(user=None, password=None):
    config = DB_CONFIG.copy()
    if user:
        config["user"] = user
    if password:
        config["password"] = password
    return mariadb.connect(**config)