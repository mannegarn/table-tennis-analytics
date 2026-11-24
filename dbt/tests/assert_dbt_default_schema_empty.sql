-- Ensure dbt never materializes stray models into public or dbt_default.
select
    table_schema,
    table_name,
    table_type
from information_schema.tables
where
    (table_schema = 'dbt_default')
    or (table_schema = 'public' and table_name <> 'schema_version')
