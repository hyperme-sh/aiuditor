"""Common transactional env; version locations and table are set by the runner."""
from alembic import context

config = context.config
connection = config.attributes['connection']
context.configure(connection=connection, version_table=config.attributes['version_table'], version_table_schema='public')
with context.begin_transaction():
    context.run_migrations()
