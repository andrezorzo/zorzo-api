"""Hand-written: put the delete rules in the database.

Django 5.2 emulates `on_delete` in Python and creates foreign keys as NO ACTION. The API and ingest write
SQL directly, so the rules of the F01-T01 contract must live in Postgres.
"""

from django.db import migrations

RULES = [
    ("answer_cache", "corpus_version", "corpus_versions", "version", "CASCADE"),
    ("answer_cache", "trace_id", "public_traces", "id", "SET NULL"),
    ("chunks", "corpus_version", "corpus_versions", "version", "CASCADE"),
    ("chunks", "document_id", "documents", "id", "CASCADE"),
    ("corpus_versions", "eval_run_id", "eval_runs", "id", "SET NULL"),
    ("documents", "corpus_version", "corpus_versions", "version", "CASCADE"),
    ("eval_results", "eval_run_id", "eval_runs", "id", "CASCADE"),
    ("log_events", "trace_id", "public_traces", "id", "SET NULL"),
    ("questions_log", "trace_id", "public_traces", "id", "SET NULL"),
    ("trace_removals", "trace_id", "public_traces", "id", "CASCADE"),
    ("trace_spans", "trace_id", "public_traces", "id", "CASCADE"),
]


def _sql(rule: str) -> str:
    statements = []
    for table, column, ref_table, ref_column, action in RULES:
        on_delete = "NO ACTION" if rule == "reverse" else action
        statements.append(
            f"""
DO $$
DECLARE name text;
BEGIN
  SELECT tc.constraint_name INTO STRICT name
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON kcu.constraint_name = tc.constraint_name
   AND kcu.table_schema = tc.table_schema
   AND kcu.table_name = tc.table_name
  WHERE tc.table_schema = current_schema() AND tc.table_name = '{table}'
    AND kcu.column_name = '{column}' AND tc.constraint_type = 'FOREIGN KEY';
  EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', name);
  EXECUTE format(
    'ALTER TABLE {table} ADD CONSTRAINT %I FOREIGN KEY ({column}) REFERENCES {ref_table} ({ref_column})
     ON DELETE {on_delete} DEFERRABLE INITIALLY DEFERRED', name);
END $$;"""
        )
    return "\n".join(statements)


class Migration(migrations.Migration):
    dependencies = [("data", "0001_initial_schema")]

    operations = [migrations.RunSQL(_sql("forward"), reverse_sql=_sql("reverse"))]
