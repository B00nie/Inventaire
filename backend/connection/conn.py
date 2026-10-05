"""Conexão preguiçosa por contexto Flask; sem conexão global compartilhada."""
import os
import psycopg2
from flask import g
from werkzeug.local import LocalProxy


class Connection:
    def init_app(self, app):
        app.teardown_appcontext(self.close)

    def _connect(self):
        if 'inventaire_db' not in g:
            missing = [key for key in ('DB_HOST', 'DB_USER', 'DB_NAME')
                       if not os.getenv(key)]
            if missing:
                raise RuntimeError('Configuração PostgreSQL pendente: ' + ', '.join(missing))
            g.inventaire_db = psycopg2.connect(
                host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', '5432'),
                user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                dbname=os.getenv('DB_NAME'), connect_timeout=3,
            )
        return g.inventaire_db

    def get_connection(self):
        return LocalProxy(self._connect)

    def close(self, error=None):
        connection = g.pop('inventaire_db', None)
        if connection is not None:
            try:
                # Encerra também leituras e erros já tratados nas rotas.
                if not connection.closed:
                    connection.rollback()
            finally:
                connection.close()
