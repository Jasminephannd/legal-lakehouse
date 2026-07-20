# Explicit schema, no crawler — crawlers cost money and drift. Column
# list intentionally excludes `jurisdiction` and `year`: those are Hive
# partition keys, encoded in the S3 path
# (silver/jurisdiction=X/year=Y/...), not written into the Parquet files
# themselves (see PARTITION_KEY_FIELDS in src/parser/handler.py — this
# table definition and that exclusion have to stay in sync).
resource "aws_glue_catalog_table" "silver_judgments" {
  name          = "silver_judgments"
  database_name = aws_glue_catalog_database.legal_lakehouse.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification        = "parquet"
    "parquet.compression" = "SNAPPY"
    EXTERNAL               = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data.id}/silver/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    # Mirrors ParsedDoc (src/parser/models.py) minus jurisdiction/year.
    # decision_date and ingested_at are typed as strings, not
    # date/timestamp: Pydantic's model_dump(mode="json") serializes them
    # to ISO-8601 strings, and pyarrow infers the column type from what's
    # actually in the data. Casting to proper date/timestamp types is
    # left to Day 2's dbt staging model — matches the plan's own
    # convention that staging (not the raw source) is where casting
    # happens.
    columns {
      name = "doc_id"
      type = "string"
    }
    columns {
      name = "doc_type"
      type = "string"
    }
    columns {
      name = "court"
      type = "string"
    }
    columns {
      name = "citation"
      type = "string"
    }
    columns {
      name = "decision_date"
      type = "string"
    }
    columns {
      name = "source_url"
      type = "string"
    }
    columns {
      name = "text"
      type = "string"
    }
    columns {
      name = "text_length"
      type = "bigint"
    }
    columns {
      name = "ingested_at"
      type = "string"
    }
  }

  partition_keys {
    name = "jurisdiction"
    type = "string"
  }

  partition_keys {
    name = "year"
    type = "string"
  }
}

output "silver_table_name" {
  value       = "${aws_glue_catalog_database.legal_lakehouse.name}.${aws_glue_catalog_table.silver_judgments.name}"
  description = "Fully-qualified name for Athena queries, e.g. SELECT * FROM legal_lakehouse.silver_judgments"
}
