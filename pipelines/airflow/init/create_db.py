"""Create the `airflow` metadata database in the shared Postgres if it is missing."""

import os

import psycopg2
from psycopg2 import sql

conn = psycopg2.connect(
    host="postgres",
    dbname="postgres",
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("select 1 from pg_database where datname = 'airflow'")
    if cur.fetchone() is None:
        cur.execute(sql.SQL("create database {}").format(sql.Identifier("airflow")))
conn.close()
