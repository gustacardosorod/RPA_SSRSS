# Análise do repositório RPA_SSRSS

## Problemas encontrados

### 1. Upsert antigo era delete + append

No fluxo anterior, o modo `upsert` removia chaves existentes no banco e depois fazia `append` com as novas linhas. Se a conexão falhasse entre essas etapas, o banco poderia ficar sem dados antigos e sem dados novos. Essa é a inconsistência mais perigosa.

**Correção aplicada:** `db_cockroach.py` agora usa staging e `UPSERT`. A tabela final precisa ter primary key compatível.

### 2. Chave ausente não bloqueava a carga

Quando a chave configurada não estava no CSV tratado ou na tabela, o fluxo podia seguir. Isso abre caminho para duplicidade.

**Correção aplicada:** carga `upsert` e `incremental` agora falha se a chave estiver ausente, vazia ou duplicada.

### 3. CSV podia pular linhas ruins

A leitura anterior aceitava `on_bad_lines="skip"`. Isso evita erro técnico, mas cria erro de indicador.

**Correção aplicada:** leitura de CSV tratado para banco agora usa `on_bad_lines="error"`.

### 4. Agent Contact por fila estava usando total diário

O relatório `Agent - Contact Handling Time 4 - Daily` possui detalhes por fila nas posições 36 a 43. A versão anterior usava os campos de total diário do agente e apenas carregava fila como contexto.

**Correção aplicada:** `f_agent_contact_fila_diario` agora usa os campos reais da fila. `f_agent_contact_diario` passou a ser derivada da tabela por fila.

### 5. Não havia auditoria bloqueante antes do banco

O processo dependia muito da operação manual e dos logs de carga. Logs contam o que aconteceu. Auditoria impede que besteira entre.

**Correção aplicada:** criado `etl_validacao.py`, com bloqueio por duplicidade, data aberta/futura e divergências entre Volume e Agent.

## Resultado com os relatórios enviados

Ao processar os quatro relatórios enviados:

- `f_volume_geral_diario.csv`: 60 linhas
- `f_volume_fila_diario.csv`: 360 linhas
- `f_agent_contact_fila_diario.csv`: 4.392 linhas
- `f_agent_contact_diario.csv`: 962 linhas
- `f_css_atendente.csv`: 29 linhas

Validação com data máxima `2026-06-29`:

- Auditoria retornou OK.
- Total de atendidas no Volume: 31.379.
- Total de ligações no Agent: 31.379.
- Diferença: 0.

Validação com data máxima `2026-06-28`:

- Carga bloqueada, pois havia dados de `2026-06-29`.
- Isso é correto para rotina automática D-1.

## Recomendação operacional

1. Use CSV como fonte oficial.
2. Receba arquivo acumulado, mas grave com `upsert` por chave.
3. Não carregue dia atual na rotina automática.
4. Gere auditoria antes do banco.
5. Só atualize Power BI após carga com status OK.
6. Migre tabelas antigas sem primary key para tabelas com primary key.

## Ordem de implantação

1. Configurar `.env` sem versionar credenciais.
2. Rodar `sql/cockroachdb_schema_melhorias.sql` em ambiente de teste.
3. Processar os relatórios localmente com `etl_contact_center_pacote.py`.
4. Conferir `auditoria_validacao_contact_center.csv`.
5. Testar `--enviar-banco --db-mode upsert` em base de teste.
6. Apontar Power BI para as views/tabelas novas.
7. Só depois substituir a rotina antiga.
