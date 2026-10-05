from psycopg2.extras import RealDictCursor

class EstatisticasRepository:
    def __init__(self, connection):
        self.conn = connection

    def get_estatisticas_conformidade(self, data_inicio: str, data_fim: str):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            consulta = """
                SELECT 
                    COALESCE(SUM(total_deteccoes), 0) AS total_deteccoes,
                    COALESCE(SUM(total_conformes), 0) AS total_conformes,
                    COALESCE(SUM(total_nao_conformes), 0) AS total_nao_conformes,
                    ROUND(
                        (COALESCE(SUM(total_conformes), 0)::numeric / 
                         NULLIF(COALESCE(SUM(total_conformes + total_nao_conformes), 0), 0)) * 100, 
                        1
                    ) AS conformidade_media
                FROM estatisticas
                WHERE data_hora BETWEEN %s AND %s;
            """

            cursor.execute(consulta, (data_inicio, data_fim))

            return dict(cursor.fetchone() or {})

    def get_estatisticas_por_setor(self, setor_id: int, data_inicio: str, data_fim: str):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            consulta = """
                SELECT 
                    COALESCE(SUM(total_deteccoes), 0) AS total_deteccoes,
                    COALESCE(SUM(total_conformes), 0) AS total_conformes,
                    COALESCE(SUM(total_nao_conformes), 0) AS total_nao_conformes,
                    ROUND(
                        (COALESCE(SUM(total_conformes), 0)::numeric / 
                         NULLIF(COALESCE(SUM(total_conformes + total_nao_conformes), 0), 0)) * 100, 
                        1
                    ) AS conformidade_media
                FROM estatisticas
                WHERE id_setor = %s AND data_hora BETWEEN %s AND %s;
            """

            cursor.execute(consulta, (setor_id, data_inicio, data_fim))

            return dict(cursor.fetchone() or {})

    def get_evolucao_conformidade(self, data_inicio: str, data_fim: str):
        """Útil para o gráfico de linha de relatórios"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            consulta = """
                SELECT 
                    TO_CHAR(data_hora, 'YYYY-MM-DD') AS dia,
                    ROUND(
                        (SUM(total_conformes)::numeric / 
                         NULLIF(SUM(total_conformes + total_nao_conformes), 0)) * 100, 
                        1
                    ) AS taxa_conformidade
                FROM estatisticas
                WHERE data_hora BETWEEN %s AND %s
                GROUP BY dia
                ORDER BY dia ASC;
            """
            cursor.execute(consulta, (data_inicio, data_fim))
            resultados = cursor.fetchall()

            conformidade_evolucao = []

            for resultado in resultados:
                conformidade_evolucao.append(dict(resultado))

            return conformidade_evolucao

    def armazenar_estatisticas(self, id_setor, total_deteccoes, total_conformes, total_nao_conformes) -> bool:
        """Armazena as estatísticas no banco de dados"""
        try:
            with self.conn.cursor() as cursor:
                insert_query = """
                    INSERT INTO estatisticas (id_setor, total_deteccoes, total_conformes, total_nao_conformes)
                    VALUES (%s, %s, %s, %s)
                """
                
                cursor.execute(insert_query, (id_setor, total_deteccoes, total_conformes, total_nao_conformes))
                self.conn.commit()
                
            return True
        except Exception as e:
            print(f"Erro ao armazenar estatísticas: {e}")
            self.conn.rollback()
            return False

    def get_quantidade_conformes(self) -> dict:
        """Obtém quantidade de conformes e não conformes"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            consulta = """
                SELECT 
                    COALESCE(SUM(total_conformes) FILTER (WHERE total_conformes >= 0), 0) AS total_conformes,
                    COALESCE(SUM(total_nao_conformes) FILTER (WHERE total_nao_conformes >= 0), 0) AS total_nao_conformes
                FROM estatisticas;
            """
            cursor.execute(consulta)
            resultado = cursor.fetchone()

            return {
                "total_conformes": int(resultado['total_conformes']),
                "total_nao_conformes": int(resultado['total_nao_conformes'])
            }