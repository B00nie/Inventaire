from flask_socketio import SocketIO
from redis import Redis
import os

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

redis_client = Redis.from_url(REDIS_URL, decode_responses=True)

socketio = SocketIO(async_mode='threading')

def emitir_evento_setor(evento: str, dados: dict, id_setor: int):
    """
    Emite um evento no Redis direcionado para a sala privada do usuário.
    Funciona no processo principal e em subprocessos (como o VisionWorker).

    Args:
        evento (str): O nome do evento a ser emitido.
        dados (dict): Os dados a serem enviados com o evento.
        id_setor (int): O ID do setor para o qual o evento será emitido.
    """

    try:
        emitter = SocketIO(message_queue=REDIS_URL)
        emitter.emit(evento, dados, to=f"setor_{id_setor}")

    except Exception as e:
        print(f"Erro ao emitir evento global: {e}")


def emitir_evento_global(evento: str, dados: dict):
    """
    Emite um evento no Redis para todos os clientes conectados.
    Funciona no processo principal e em subprocessos (como o VisionWorker).

    Args:
        evento (str): O nome do evento a ser emitido.
        dados (dict): Os dados a serem enviados com o evento.
    """

    try:
        emitter = SocketIO(message_queue=REDIS_URL)
        emitter.emit(evento, dados)

    except Exception as e:
        print(f"Erro ao emitir evento global: {e}")