from db_cockroach import testar_conexao, preparar_database

try:
    print("Preparando database...")
    db = preparar_database()
    print("Database preparado:", db)

    print("Testando conexão...")
    resultado = testar_conexao(db)
    print("Conexão OK:")
    print(resultado)

except Exception as e:
    print("Falha na conexão:")
    print(e)