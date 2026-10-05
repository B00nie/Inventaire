"""Composição HTTP mínima do Inventaire: usuários e produtos."""
from pathlib import Path
from datetime import timedelta
import os

from dotenv import load_dotenv

# Ambiente do processo tem precedência; caminho independente do diretório atual.
load_dotenv(Path(__file__).resolve().parent / '.env')

from flask import Flask, jsonify
from flask_cors import CORS
from flask_session import Session
from werkzeug.exceptions import BadRequest, UnsupportedMediaType
import redis

from connection.conn import Connection
from controller.usuario_routes import create_user_bp
from controller.produto_routes import create_produto_bp
from controller.movimentacao_routes import create_movimentacao_bp
from controller.rastreamento_routes import create_rastreamento_bp
from controller.contagem_routes import create_contagem_bp
from controller.relatorios_routes import create_relatorio_bp
from controller.fornecedor_routes import create_fornecedor_bp


def create_app():
    app = Flask(__name__, static_folder=None)
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        raise RuntimeError('Configure SECRET_KEY no ambiente ou em backend/.env.')
    dev_insecure = os.getenv('DEV_INSECURE', 'false').lower() == 'true'
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_TYPE='redis',
        SESSION_PERMANENT=True,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        SESSION_USE_SIGNER=True,
        SESSION_KEY_PREFIX='inventaire:session:',
        SESSION_REDIS=redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
            socket_connect_timeout=3, socket_timeout=3,
        ),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not dev_insecure,
        SESSION_COOKIE_SAMESITE='Lax',
    )
    Session(app)
    CORS(app, supports_credentials=True,
         origins=[r'^http://localhost(?::\d+)?$', r'^http://127\.0\.0\.1(?::\d+)?$']
         if dev_insecure else [origin.strip() for origin in
                               os.getenv('CORS_ORIGINS', '').split(',') if origin.strip()])

    database = Connection()
    database.init_app(app)
    app.register_blueprint(create_user_bp(database.get_connection()))
    app.register_blueprint(create_produto_bp(database.get_connection()))
    app.register_blueprint(create_movimentacao_bp(database.get_connection()))
    app.register_blueprint(create_rastreamento_bp(database.get_connection()))
    app.register_blueprint(create_contagem_bp(database.get_connection()))
    app.register_blueprint(create_relatorio_bp(database.get_connection()))
    app.register_blueprint(create_fornecedor_bp(database.get_connection()))

    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def invalid_json(error):
        return jsonify({'message': 'Payload JSON inválido'}), 400

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'message': 'Erro interno do servidor'}), 500

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
