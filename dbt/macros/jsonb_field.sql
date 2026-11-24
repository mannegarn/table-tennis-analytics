{% macro jsonb_text(obj, camel_key, pascal_key) -%}
    nullif(
        btrim(
            coalesce({{ obj }} ->> '{{ camel_key }}', {{ obj }} ->> '{{ pascal_key }}')
        ),
        ''
    )
{%- endmacro %}

{% macro jsonb_cast_int(obj, camel_key, pascal_key, sql_type='integer') -%}
    case
        when
            btrim(
                coalesce({{ obj }} ->> '{{ camel_key }}', {{ obj }} ->> '{{ pascal_key }}')
            ) ~ '^[0-9]+$'
            then
                btrim(
                    coalesce({{ obj }} ->> '{{ camel_key }}', {{ obj }} ->> '{{ pascal_key }}')
                )::{{ sql_type }}
    end
{%- endmacro %}
