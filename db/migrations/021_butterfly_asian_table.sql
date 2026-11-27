-- 021: Reclassify Butterfly tables from European to Asian group.
--
-- Butterfly tables have a grippier surface closer to Asian-style tables.

INSERT INTO analytics.dim_table_brands (table_sponsor, table_group, notes)
VALUES (
    'Butterfly',
    'Asian',
    'Grippier surface grouped with Asian-style tables'
)
ON CONFLICT (table_sponsor) DO UPDATE
SET
    table_group = excluded.table_group,
    notes = excluded.notes;
