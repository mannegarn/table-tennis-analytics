{% macro jsonb_text(obj, camel_key, pascal_key=none) -%}
    {%- if pascal_key is not none -%}
        nullif(
            btrim(
                coalesce({{ obj }} ->> '{{ camel_key }}', {{ obj }} ->> '{{ pascal_key }}')
            ),
            ''
        )
    {%- else -%}
        nullif(btrim({{ obj }} ->> '{{ camel_key }}'), '')
    {%- endif -%}
{%- endmacro %}

{% macro jsonb_cast_int(obj, camel_key, pascal_key=none, sql_type='integer') -%}
    {%- set expr -%}
        {%- if pascal_key is not none -%}
            btrim(
                coalesce({{ obj }} ->> '{{ camel_key }}', {{ obj }} ->> '{{ pascal_key }}')
            )
        {%- else -%}
            btrim({{ obj }} ->> '{{ camel_key }}')
        {%- endif -%}
    {%- endset -%}
    case
        when {{ expr }} ~ '^[0-9]+$'
            then ({{ expr }})::{{ sql_type }}
    end
{%- endmacro %}

{% macro parse_wtt_timestamp_utc(col) -%}
    case
        when
            {{ col }} ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}$'
            and {{ col }} not like '01/01/0001%'
            then to_timestamp({{ col }} || ' +00', 'MM/DD/YYYY HH24:MI:SS TZH')
        when
            {{ col }}
            ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
            and {{ col }} not like '0001-01-01%'
            then {{ col }}::timestamptz
        when
            {{ col }} ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?$'
            and {{ col }} not like '0001-01-01%'
            then {{ col }}::timestamp at time zone 'UTC'
    end
{%- endmacro %}

{% macro parse_wtt_timestamp_local(col) -%}
    case
        when
            {{ col }} ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}$'
            and {{ col }} not like '01/01/0001%'
            then
                to_timestamp({{ col }} || ' +00', 'MM/DD/YYYY HH24:MI:SS TZH')
                at time zone 'UTC'
        when
            {{ col }} ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?$'
            and {{ col }} not like '0001-01-01%'
            then {{ col }}::timestamp
    end
{%- endmacro %}
