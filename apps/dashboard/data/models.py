"""Schema owner for the whole system (LLD "Data").

The API and ingest reflect these tables at startup and write SQL against them, so every default that
matters to them is a database default (`db_default`), not a Python-side one.
"""

from django.contrib.postgres.functions import RandomUUID
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

OUTCOMES = ("answered", "abstained", "declined", "blocked", "degraded", "cached")
LANGS = ("en", "pt")


def _in(field: str, values: tuple[str, ...]) -> Q:
    return Q(**{f"{field}__in": values})


class CorpusVersion(models.Model):
    version = models.BigAutoField(primary_key=True)
    status = models.TextField(db_default="candidate")
    embed_model_tag = models.TextField()
    created_at = models.DateTimeField(db_default=Now())
    activated_at = models.DateTimeField(null=True)
    eval_run = models.ForeignKey("EvalRun", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "corpus_versions"
        constraints = [
            models.CheckConstraint(
                condition=_in("status", ("candidate", "active", "retired", "rejected")),
                name="corpus_versions_status_check",
            ),
            models.UniqueConstraint(
                fields=["status"], condition=Q(status="active"), name="corpus_versions_one_active"
            ),
        ]


class Document(models.Model):
    corpus_version = models.ForeignKey(CorpusVersion, on_delete=models.CASCADE, db_column="corpus_version")
    doc_key = models.TextField()
    title = models.TextField()
    kind = models.TextField()
    lang = models.TextField()
    authority = models.SmallIntegerField()
    source_url = models.TextField(null=True)
    updated = models.DateField(null=True)
    hash = models.TextField()

    class Meta:
        db_table = "documents"
        constraints = [
            models.CheckConstraint(
                condition=_in(
                    "kind", ("resume", "narrative", "faq", "repo", "project_doc", "site_doc", "post")
                ),
                name="documents_kind_check",
            ),
            models.CheckConstraint(condition=_in("lang", LANGS), name="documents_lang_check"),
            models.CheckConstraint(
                condition=Q(authority__gte=1, authority__lte=3), name="documents_authority_check"
            ),
            models.UniqueConstraint(fields=["corpus_version", "doc_key"], name="documents_version_doc_key"),
        ]


class Chunk(models.Model):
    """`embedding` and `tsv` are added by F01-T02, which settles the embedding model."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    corpus_version = models.ForeignKey(CorpusVersion, on_delete=models.CASCADE, db_column="corpus_version")
    heading_path = models.TextField()
    content = models.TextField()
    tokens = models.IntegerField()
    hash = models.TextField()

    class Meta:
        db_table = "chunks"
        constraints = [models.UniqueConstraint(fields=["corpus_version", "hash"], name="chunks_version_hash")]


class PublicTrace(models.Model):
    id = models.UUIDField(primary_key=True, db_default=RandomUUID())
    created_at = models.DateTimeField(db_default=Now(), db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    outcome = models.TextField()
    intent = models.TextField(null=True)
    lang = models.TextField(null=True)
    corpus_version = models.BigIntegerField(null=True)
    model_config_version = models.TextField(null=True)
    latency_ms = models.IntegerField(null=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, db_default=0)
    public_ok = models.BooleanField(db_default=False)
    removed_at = models.DateTimeField(null=True)
    payload = models.JSONField(db_default={})

    class Meta:
        db_table = "public_traces"
        constraints = [
            models.CheckConstraint(condition=_in("outcome", OUTCOMES), name="public_traces_outcome_check")
        ]


class AnswerCache(models.Model):
    """`embedding` is added by F01-T02, which settles the embedding model."""

    question_scrubbed = models.TextField()
    lang = models.TextField()
    corpus_version = models.ForeignKey(CorpusVersion, on_delete=models.CASCADE, db_column="corpus_version")
    model_config_version = models.TextField()
    answer = models.JSONField()
    trace = models.ForeignKey(PublicTrace, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(db_default=Now())
    expires_at = models.DateTimeField()
    hits = models.IntegerField(db_default=0)

    class Meta:
        db_table = "answer_cache"
        constraints = [models.CheckConstraint(condition=_in("lang", LANGS), name="answer_cache_lang_check")]


class TraceSpan(models.Model):
    trace = models.ForeignKey(PublicTrace, on_delete=models.CASCADE)
    step = models.TextField()
    started_at = models.DateTimeField()
    latency_ms = models.IntegerField()
    model = models.TextField(null=True)
    provider = models.TextField(null=True)
    fallback_reason = models.TextField(null=True)
    tokens_in = models.IntegerField(null=True)
    tokens_out = models.IntegerField(null=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, db_default=0)

    class Meta:
        db_table = "trace_spans"


class TraceRemoval(models.Model):
    trace = models.ForeignKey(PublicTrace, on_delete=models.CASCADE)
    removed_at = models.DateTimeField(db_default=Now())
    reason = models.TextField(null=True)

    class Meta:
        db_table = "trace_removals"


class QuestionLog(models.Model):
    trace = models.ForeignKey(PublicTrace, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(db_default=Now())
    question_scrubbed = models.TextField()
    intent = models.TextField(null=True)
    outcome = models.TextField()

    class Meta:
        db_table = "questions_log"
        constraints = [
            models.CheckConstraint(condition=_in("outcome", OUTCOMES), name="questions_log_outcome_check")
        ]


class DefenseEvent(models.Model):
    created_at = models.DateTimeField(db_default=Now(), db_index=True)
    trace_id = models.UUIDField(null=True)
    layer = models.TextField()
    category = models.TextField()
    action = models.TextField()
    example_scrubbed = models.TextField(null=True)

    class Meta:
        db_table = "defense_events"


class LogEvent(models.Model):
    at = models.DateTimeField(db_default=Now(), db_index=True)
    level = models.TextField()
    source = models.TextField()
    component = models.TextField()
    event = models.TextField()
    trace = models.ForeignKey(PublicTrace, null=True, on_delete=models.SET_NULL)
    fields = models.JSONField(db_default={})

    class Meta:
        db_table = "log_events"
        constraints = [
            models.CheckConstraint(
                condition=_in("level", ("debug", "info", "warning", "error")), name="log_events_level_check"
            ),
            models.CheckConstraint(
                condition=_in("source", ("app", "langsmith")), name="log_events_source_check"
            ),
        ]


class RateBucket(models.Model):
    key = models.TextField(primary_key=True)
    tokens = models.FloatField()
    refilled_at = models.DateTimeField()

    class Meta:
        db_table = "rate_buckets"


class BudgetDay(models.Model):
    day = models.DateField(primary_key=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, db_default=0)
    requests = models.IntegerField(db_default=0)
    paid_disabled_at = models.DateTimeField(null=True)
    degraded_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "budget_daily"


class EvalRun(models.Model):
    created_at = models.DateTimeField(db_default=Now())
    corpus_version = models.BigIntegerField()
    model_config_version = models.TextField()
    run_kind = models.TextField()
    dataset = models.TextField()
    metrics = models.JSONField()
    passed = models.BooleanField()

    class Meta:
        db_table = "eval_runs"
        constraints = [
            models.CheckConstraint(
                condition=_in("run_kind", ("pre_release", "nightly")), name="eval_runs_run_kind_check"
            )
        ]


class EvalResult(models.Model):
    eval_run = models.ForeignKey(EvalRun, on_delete=models.CASCADE)
    question_id = models.TextField()
    lang = models.TextField()
    layer = models.TextField()
    career_fact = models.BooleanField(db_default=False)
    passed = models.BooleanField()
    reason = models.TextField(null=True)
    expected = models.TextField(null=True)
    answer = models.TextField(null=True)

    class Meta:
        db_table = "eval_results"
        constraints = [
            models.CheckConstraint(condition=_in("lang", LANGS), name="eval_results_lang_check"),
            models.CheckConstraint(
                condition=_in("layer", ("retrieval", "generation", "end_to_end")),
                name="eval_results_layer_check",
            ),
        ]


class ExternalSnapshot(models.Model):
    taken_at = models.DateTimeField(db_default=Now())
    source = models.TextField()
    kind = models.TextField()
    payload = models.JSONField()

    class Meta:
        db_table = "external_snapshots"
        constraints = [
            models.CheckConstraint(
                condition=_in("source", ("langsmith", "uptimerobot")), name="external_snapshots_source_check"
            )
        ]


class AlertState(models.Model):
    """`site_down` has no row: UptimeRobot sends it by email itself (LLD "6.10 Alerts")."""

    kind = models.TextField(primary_key=True)
    open_since = models.DateTimeField(null=True)
    last_sent_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "alert_states"
        constraints = [
            models.CheckConstraint(
                condition=_in(
                    "kind",
                    (
                        "budget_exhausted",
                        "providers_down",
                        "backup_failed",
                        "restore_failed",
                        "eval_regression",
                    ),
                ),
                name="alert_states_kind_check",
            )
        ]
