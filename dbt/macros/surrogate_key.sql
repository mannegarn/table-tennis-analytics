{% macro surrogate_key(source_system, parts) -%}
    ('x' || left(md5({{ source_system }}::text
    {%- for p in parts %} || '|' || {{ p }}::text{% endfor %}), 15))::bit(60)::bigint
{%- endmacro %}
