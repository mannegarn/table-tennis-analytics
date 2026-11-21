{% macro player_sk(ittf_id, source_system=none, source_player_id=none) -%}
    {%- if source_system is not none and source_player_id is not none -%}
        coalesce(
            {{ surrogate_key("'ittf'", [ittf_id]) }},
            {{ surrogate_key(source_system, [source_player_id]) }}
        )
    {%- else -%}
        {{ surrogate_key("'ittf'", [ittf_id]) }}
    {%- endif -%}
{%- endmacro %}

{% macro source_player_sk(source_system, source_player_id) -%}
    {{ surrogate_key(source_system, [source_player_id]) }}
{%- endmacro %}
