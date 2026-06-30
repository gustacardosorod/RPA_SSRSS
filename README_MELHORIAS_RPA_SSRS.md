# RPA_SSRS - Versão com melhores práticas de carga

Esta versão reorganiza o fluxo para reduzir inconsistências no banco e no Power BI. O objetivo é simples: parar de tratar carga como append esperançoso, esse patrimônio cultural dos dashboards que não batem.

## Principais mudanças

1. **Banco com staging + upsert**
   - O modo `upsert` não usa mais `DELETE` separado seguido de `append`.
   - A carga valida chave, grava em staging e executa `UPSERT` na tabela final.
   - Se a tabela final já existir sem primary key compatível, a carga falha com mensagem clara.

2. **Chave obrigatória**
   - Carga `upsert` e `incremental` exige chave configurada.
   - Se a chave faltar no CSV tratado, a carga é bloqueada.
   - Se houver duplicidade na chave dentro da carga, a carga é bloqueada.

3. **Agent Contact corrigido**
   - A tabela `f_agent_contact_fila_diario` agora usa a granularidade correta por fila.
   - A tabela `f_agent_contact_diario` é derivada da tabela por fila.
   - Isso melhora auditoria entre `Volume` e `Agent`.

4. **Auditoria antes do banco**
   - Novo arquivo: `etl_validacao.py`.
   - Verifica duplicidades, datas abertas/futuras, Volume geral vs Volume por fila e Volume vs Agent.

5. **Orquestrador de pacote**
   - Novo arquivo: `etl_contact_center_pacote.py`.
   - Processa Volume, Agent, CSS, valida e opcionalmente envia ao banco.

6. **Manifesto dos arquivos de entrada**
   - Gera `manifest.json` com nome, tamanho, hash SHA256 e data de modificação.
   - Ajuda a rastrear qual arquivo gerou qual carga.

7. **Secrets removidos do exemplo**
   - `.env.example` e `.streamlit/secrets.toml.example` foram higienizados.
   - Nunca subir senha real. Parece óbvio, mas a civilização insiste em testar.

## Fluxo recomendado

```text
SSRS / assinatura
        ↓
Pasta RAW do lote
        ↓
etl_contact_center_pacote.py
        ↓
CSVs tratados + auditoria
        ↓
staging CockroachDB
        ↓
UPSERT nas tabelas finais
        ↓
Power BI via views/tabelas finais
```

## Como processar apenas CSVs locais

```powershell
python etl_contact_center_pacote.py `
  --entrada "C:\RPA_SSRSS_REPO\entrada\29_06_2026" `
  --saida "C:\RPA_SSRSS_REPO\saida" `
  --logs "C:\RPA_SSRSS_REPO\LOGS" `
  --data-maxima 2026-06-28
```

## Como processar e enviar ao banco

```powershell
python etl_contact_center_pacote.py `
  --entrada "C:\RPA_SSRSS_REPO\entrada\29_06_2026" `
  --saida "C:\RPA_SSRSS_REPO\saida" `
  --logs "C:\RPA_SSRSS_REPO\LOGS" `
  --data-maxima 2026-06-28 `
  --enviar-banco `
  --db-mode upsert
```

## Regra operacional recomendada

- Rotina automática: carregar **D-1 fechado**.
- Reprocessamento: carregar período fechado explícito.
- CSV acumulado pode ser usado, desde que a chave e a auditoria estejam ativas.
- Nunca usar `append` para carga recorrente de relatório acumulado.

## Arquivos novos ou alterados

- `db_cockroach.py`: gravação segura com staging + upsert.
- `etl_agent_contact_diario.py`: gera diário e fila corretamente.
- `etl_validacao.py`: auditoria dos CSVs processados.
- `etl_contact_center_pacote.py`: orquestra ETL + validação + banco.
- `sql/cockroachdb_schema_melhorias.sql`: schema recomendado.
- `sql/auditoria_contact_center.sql`: consultas de auditoria no banco.
- `.env.example`: exemplo sem credenciais reais.

## Atenção para bases antigas

Se as tabelas antigas já existem no CockroachDB sem primary key, o novo `upsert` vai bloquear a carga. Isso é intencional.

Caminho mais seguro:

1. Criar tabelas novas com primary key usando `sql/cockroachdb_schema_melhorias.sql`.
2. Rodar uma carga full validada.
3. Conferir auditoria.
4. Apontar o Power BI para as novas tabelas/views.
5. Só depois aposentar as tabelas antigas.

## Validações mínimas esperadas

Antes de atualizar o Power BI, a auditoria deve retornar somente `OK` ou avisos controlados.

Validações críticas:

- Sem duplicidade por chave.
- Sem dia atual/futuro na rotina automática.
- `f_volume_geral_diario.atendidas` = soma de `f_agent_contact_fila_diario.total_ligacoes`.
- `f_volume_geral_diario.recebidas/atendidas` = soma de `f_volume_fila_diario`.
