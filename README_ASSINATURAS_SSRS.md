# RPA SSRS por assinatura direta

Este pacote foi ajustado para o cenário em que o SSRS grava os relatórios direto em uma pasta de assinatura e substitui os arquivos antigos.

## Caminho informado

```text
H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas
```

Para Tarefa Agendada do Windows, prefira trocar `H:` pelo caminho UNC real, por exemplo:

```text
\\SERVIDOR\Compartilhamento\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas
```

Drive mapeado em tarefa agendada some como funcionário em véspera de feriado. Use UNC.

## Arquivos esperados na pasta de assinatura

```text
Volume 4 - Daily.csv
Agent - Contact Handling Time 4 - Daily.csv
Script Result 5 - Agent Volume.csv
Script Result 3 - Queue Volume per Day.csv
```

O `Script Result 3 - Queue Volume per Day.csv` é importante porque ele traz CSS por data/fila e permite montar o CSS diário corretamente.

## Como o fluxo funciona

1. O SSRS grava/substitui os CSVs na pasta `Assinaturas`.
2. `rpa_ssrs_assinaturas.py` copia esses arquivos para uma pasta histórica local:
   `entrada_assinaturas\AAAA_MM_DD`.
3. O ETL processa todo o histórico preservado.
4. Os CSVs finais são gravados em `saida`.
5. Opcionalmente, os CSVs finais são enviados para o CockroachDB.

Essa etapa de arquivamento é o ponto mais importante. Sem isso, se o SSRS substituir o arquivo no sábado e o RPA só rodar na segunda, os dados intermediários podem virar lenda urbana.

## Primeiro teste sem banco

Edite o arquivo:

```text
applocais\testar_assinaturas_sem_banco.bat
```

Ajuste o caminho `ASSINATURAS` se necessário e execute.

Ou rode direto:

```bat
python rpa_ssrs_assinaturas.py --assinaturas "H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas"
```

## Rodar com banco

Crie um `.env` a partir do `.env.example`:

```text
COCKROACH_DATABASE_URL="postgresql://usuario:SENHA@host:26257/defaultdb?sslmode=verify-full"
COCKROACH_DATABASE_NAME="rpa_ssrs"
```

Depois execute:

```bat
python rpa_ssrs_assinaturas.py ^
  --assinaturas "H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas" ^
  --enviar-banco ^
  --db-mode upsert
```

## Melhor prática para o banco

Use `--db-mode upsert` nesse cenário.

Motivo: você já tem uma base no banco. O modo `upsert` preserva o histórico existente, remove no banco somente as chaves presentes na nova saída do ETL e insere a versão mais recente dessas linhas. Assim o RPA não derruba a tabela inteira, não apaga meses antigos e ainda corrige dias já carregados quando o SSRS trouxer alguma alteração.

## Criar tarefa agendada diária

1. Abra **Agendador de Tarefas**.
2. Crie uma tarefa básica.
3. Dispare diariamente depois do horário da assinatura SSRS.
4. Marque para rodar também sábado e domingo.
5. Ação:
   ```text
   Programa/script:
   C:\...\RPA_SSRS_ASSINATURAS_INCREMENTAL\applocais\rodar_assinaturas_ssrs.bat
   ```
6. Em **Iniciar em**, use:
   ```text
   C:\...\RPA_SSRS_ASSINATURAS_INCREMENTAL\applocais
   ```

Recomendação: agende o Power BI para atualizar depois dessa tarefa, não antes. O Power BI não é vidente, embora às vezes ele erre com confiança suficiente para parecer.

## Ordem recomendada de horários

```text
05:30 - SSRS gera arquivos
05:45 - RPA arquiva + trata + envia ao banco
06:15 - Power BI atualiza
```

## Saídas principais para o BI

```text
dim_atendentes.csv
f_agent_contact_diario.csv
f_volume_geral_diario.csv
f_volume_fila_diario.csv
f_css_atendente.csv
f_css_geral_diario.csv
f_indicadores_gerais.csv
f_indicadores_gerais_periodo.csv
```

Também são geradas tabelas detalhadas de CSS, fila e agentes.


## Modo correto quando o banco já tem histórico

Como já existe base no banco, **não use `replace` na rotina diária**. Use:

```bat
--db-mode upsert
```

Diferença prática:

- `upsert`: preserva o banco e atualiza somente as chaves que aparecem nos CSVs tratados. Melhor para produção.
- `incremental`: grava apenas chaves novas. Não corrige valores de datas já existentes.
- `append`: só empilha linhas. Pode duplicar. Use apenas em testes controlados.
- `replace`: derruba e recria a tabela. Só use em carga inicial/homologação quando tiver certeza absoluta. Sim, absoluta mesmo, não aquele “acho que sim” que vira reunião às 18h.

---

## Dashboard Streamlit

Este pacote também possui `app.py`, um gerenciador visual para:

- listar arquivos da pasta Assinaturas;
- identificar lotes completos;
- processar ETL;
- enviar ao CockroachDB em modo `upsert`;
- limpar a pasta Assinaturas somente após banco OK.

Consulte `README_GERENCIADOR_STREAMLIT.md`.
