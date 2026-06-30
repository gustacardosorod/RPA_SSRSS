-- Auditorias rápidas para rodar no CockroachDB antes de atualizar o Power BI.

-- 1) Duplicidade por chave
SELECT data, count(*) AS qtd
FROM f_volume_geral_diario
GROUP BY data
HAVING count(*) > 1;

SELECT data, fila, count(*) AS qtd
FROM f_volume_fila_diario
GROUP BY data, fila
HAVING count(*) > 1;

SELECT data, atendente_id, grupo, fila, count(*) AS qtd
FROM f_agent_contact_fila_diario
GROUP BY data, atendente_id, grupo, fila
HAVING count(*) > 1;

-- 2) Volume geral precisa bater com soma por fila
SELECT
    g.data,
    CAST(NULLIF(g.recebidas, '') AS INT8) AS recebidas_geral,
    SUM(CAST(NULLIF(f.recebidas, '') AS INT8)) AS recebidas_fila,
    CAST(NULLIF(g.atendidas, '') AS INT8) AS atendidas_geral,
    SUM(CAST(NULLIF(f.atendidas, '') AS INT8)) AS atendidas_fila
FROM f_volume_geral_diario g
JOIN f_volume_fila_diario f ON f.data = g.data
GROUP BY g.data, g.recebidas, g.atendidas
HAVING CAST(NULLIF(g.recebidas, '') AS INT8) <> SUM(CAST(NULLIF(f.recebidas, '') AS INT8))
    OR CAST(NULLIF(g.atendidas, '') AS INT8) <> SUM(CAST(NULLIF(f.atendidas, '') AS INT8));

-- 3) Atendidas do Volume precisam bater com ligações dos agentes
SELECT
    v.data,
    CAST(NULLIF(v.atendidas, '') AS INT8) AS volume_atendidas,
    SUM(CAST(NULLIF(a.total_ligacoes, '') AS INT8)) AS agent_total_ligacoes
FROM f_volume_geral_diario v
JOIN f_agent_contact_fila_diario a ON a.data = v.data
GROUP BY v.data, v.atendidas
HAVING CAST(NULLIF(v.atendidas, '') AS INT8) <> SUM(CAST(NULLIF(a.total_ligacoes, '') AS INT8));

-- 4) Dia atual/futuro não deveria entrar na rotina automática
SELECT 'f_volume_geral_diario' AS tabela, data FROM f_volume_geral_diario WHERE data >= CAST(current_date() AS STRING)
UNION ALL
SELECT 'f_volume_fila_diario' AS tabela, data FROM f_volume_fila_diario WHERE data >= CAST(current_date() AS STRING)
UNION ALL
SELECT 'f_agent_contact_fila_diario' AS tabela, data FROM f_agent_contact_fila_diario WHERE data >= CAST(current_date() AS STRING);
