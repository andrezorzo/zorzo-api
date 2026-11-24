"""Vector and full-text columns (F01-T02).

The columns are NOT NULL with no default: `chunks` and `answer_cache` are empty in every environment when
this runs (no corpus has been ingested before F01-T02). `preserve_default=False` keeps the placeholder
default out of the database, so a write that forgets the column fails instead of storing a fake vector.
"""

import django.contrib.postgres.indexes
import django.contrib.postgres.search
import pgvector.django.indexes
import pgvector.django.vector
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("data", "0002_database_level_delete_rules")]

    operations = [
        migrations.AddField(
            model_name="chunk",
            name="embedding",
            field=pgvector.django.vector.VectorField(dimensions=1024, default=[0.0] * 1024),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chunk",
            name="tsv",
            field=django.contrib.postgres.search.SearchVectorField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="answercache",
            name="embedding",
            field=pgvector.django.vector.VectorField(dimensions=1024, default=[0.0] * 1024),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="chunk",
            index=pgvector.django.indexes.HnswIndex(
                fields=["embedding"], name="chunks_embedding_hnsw", opclasses=["vector_cosine_ops"]
            ),
        ),
        migrations.AddIndex(
            model_name="chunk",
            index=django.contrib.postgres.indexes.GinIndex(fields=["tsv"], name="chunks_tsv_gin"),
        ),
        migrations.AddIndex(
            model_name="answercache",
            index=pgvector.django.indexes.HnswIndex(
                fields=["embedding"], name="answer_cache_embedding_hnsw", opclasses=["vector_cosine_ops"]
            ),
        ),
    ]
