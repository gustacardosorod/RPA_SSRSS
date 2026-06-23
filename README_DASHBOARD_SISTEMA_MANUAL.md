# Dashboard Sistema Manual SSRS

Esta versão remove a leitura da pasta de rede/assinaturas e trabalha somente com upload manual dos CSVs exportados do SSRS.

## Regras principais

1. A importação é manual via Streamlit.
2. O painel exige um lote por vez.
3. A data oficial do lote vem do parâmetro `Dias` do relatório `Script Result 5 - Agent Volume`.
4. Se o parâmetro vier como `All`, a carga é bloqueada.
5. Se a data já existir no banco, a carga é bloqueada.
6. O envio ao banco usa `UPSERT` fixo.
7. O painel registra a data importada na tabela `controle_importacao_ssrs_manual`.
8. Para evitar subir histórico inteiro novamente, o ETL do upload manual roda em uma pasta isolada por execução.

## Arquivos esperados no upload

- `Volume 4 - Daily.csv`
- `Agent - Contact Handling Time 4 - Daily.csv`
- `Script Result 5 - Agent Volume.csv`
- `Script Result 3 - Queue Volume per Day.csv`

## Como rodar

```powershell
cd C:\RPA_SSRS
.venv\Scripts\activate
streamlit run app.py
```

## Configuração do banco

Crie ou ajuste:

```text
C:\RPA_SSRS\.streamlit\secrets.toml
```

Exemplo:

```toml
[cockroachdb]
database_url = "postgresql://rpa_user:SENHA@bulky-marmot-27729.j77.aws-us-east-1.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full"
database_name = "rpa_ssrs"
```

Se a senha tiver caracteres especiais, codifique para URL.
