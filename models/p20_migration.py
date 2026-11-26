"""Additive catalog migration; original identities and business cursors stay intact."""
from sqlalchemy import inspect, text


def migrate_catalog(engine):
    additions = {
        'chats': [('peer_id', 'VARCHAR'), ('peer_type', 'VARCHAR'),
                  ('telegram_title', 'VARCHAR'), ('username', 'VARCHAR'),
                  ('member_state', "VARCHAR DEFAULT 'unknown'"), ('verified_at', 'TIMESTAMP')],
        'collected_messages': [('received_at', 'TIMESTAMP')],
    }
    with engine.begin() as connection:
        for table, fields in additions.items():
            existing = {c['name'] for c in inspect(connection).get_columns(table)}
            for name, definition in fields:
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
        unique = inspect(connection).get_unique_constraints('chats')
        if not any(c['column_names'] == ['peer_id'] for c in unique):
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS unique_chat_peer_id ON chats (peer_id)'))
