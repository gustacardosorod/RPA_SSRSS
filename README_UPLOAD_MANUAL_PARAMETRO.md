# RPA SSRS - Upload manual com data do parâmetro do relatório

Este pacote foi ajustado para o cenário em que o relatório será baixado manualmente no SSRS e enviado pelo Streamlit.

## Regra principal do CSS por agente

O arquivo `Script Result 5 - Agent Volume.csv` só será tratado como CSS diário por agente quando o parâmetro do cabeçalho vier com uma data específica:

```text
- Dias:    2026-06-22
```

Se vier assim, o app bloqueia a carga diária:

```text
- Dias: All
```

Motivo: `All` é acumulado do período/mês e não pode virar diário por agente por mágica. O RPA usa a data do parâmetro `Dias` do `Script Result 5` como data do lote.

## Como usar no Streamlit

1. Abra o dashboard:

```bat
applocais\abrir_dashboard_streamlit.bat
```

Ou manualmente:

```bat
streamlit run app.py
```

2. Na barra lateral, selecione:

```text
Origem dos arquivos = Upload manual
```

3. Mantenha marcado:

```text
Exigir Day/Dias explícito no Script Result 5
```

4. Envie os CSVs baixados do SSRS:

```text
Volume 4 - Daily.csv
Agent - Contact Handling Time 4 - Daily.csv
Script Result 5 - Agent Volume.csv
Script Result 3 - Queue Volume per Day.csv
```

5. O app mostra a validação:

```text
Relatorio | Parametro_Dias | Data_Parametro | Status_Dia
css_agent | 2026-06-22     | 2026-06-22     | dia explícito
```

6. Clique em:

```text
Processar arquivos enviados
```

7. Se `Enviar upload ao banco` estiver marcado, o app faz UPSERT no CockroachDB.

## Banco de dados

Para base já existente, use sempre:

```text
Modo banco = upsert
```

As chaves de CSS foram ajustadas para não dependerem de `Data_Atualizacao_Carga`, evitando duplicidade quando o mesmo dia for reprocessado.

## Atenção

No Streamlit Cloud, o app não lê `H:` nem `\\VANADIS` diretamente. Para a pasta de rede, hospede em VM/servidor interno. Para Cloud, use upload manual.
