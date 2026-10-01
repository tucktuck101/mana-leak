#!/bin/bash
# Runs once on first Postgres volume initialisation.
# Creates separate users/databases for the application and Langfuse.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
  CREATE USER mana_leak WITH PASSWORD '${MANA_LEAK_DB_PASSWORD}';
  CREATE DATABASE mana_leak OWNER mana_leak;
  CREATE DATABASE mana_leak_test OWNER mana_leak;
  CREATE USER langfuse WITH PASSWORD '${LANGFUSE_DB_PASSWORD}';
  CREATE DATABASE langfuse OWNER langfuse;
SQL

# pgvector is needed by the application database and its test counterpart.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname mana_leak <<-SQL
  CREATE EXTENSION IF NOT EXISTS vector;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname mana_leak_test <<-SQL
  CREATE EXTENSION IF NOT EXISTS vector;
SQL
