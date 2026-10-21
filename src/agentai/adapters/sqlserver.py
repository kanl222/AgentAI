"""Read-only SQL Server metadata adapter."""
import os

def sql_metadata():
    if not os.getenv('SQL_CONNECTION_STRING'):
        return 'SQL disabled'
    import pyodbc
    with pyodbc.connect(os.environ['SQL_CONNECTION_STRING'],timeout=5,autocommit=True) as connection:
        cursor = connection.cursor()
        cursor.timeout = 10
        cursor.execute('SELECT TOP (500) TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION')
        return [tuple(row) for row in cursor.fetchall()]
