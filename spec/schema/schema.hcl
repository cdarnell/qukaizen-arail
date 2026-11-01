// ARAIL 2.0 relational schema — the source of truth for SQLite.
//
// Compiled by Atlas. Never hand-edit the database; edit this file and run
// `./arailctl db apply`. Every migration is linted (`atlas migrate lint`)
// and lint failures block.
//
// Schema versioning is GLOBAL, not per-world. All worlds share one entity
// schema, one embedding model, and one vector dimension at any given spec
// version. Per-world or per-user variation in embedding model or dimension
// is therefore corruption by definition, and `./arailctl db doctor` reports
// it as an integrity violation rather than as configuration.
//
// Storage split:
//   SQLite     — worlds, entities, relations, mutable state, resolution
//   LanceDB    — embeddings, generated content, semantic retrieval
//   Filesystem — large binary artifacts, referenced by path

schema "main" {}

// ---------------------------------------------------------------------------
// schema_version — one row per applied spec version.
// ---------------------------------------------------------------------------
table "schema_version" {
  schema = schema.main

  column "version" {
    null = false
    type = integer
  }
  column "spec_sha256" {
    null    = false
    type    = text
    comment = "sha256 of the concatenated spec tree that produced this version"
  }
  column "applied_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.version]
  }
}

// ---------------------------------------------------------------------------
// worlds — the resolution root. Identity is never positional.
//
// `user_id` is the tenant. In the 1.x layout the tenant was the World
// instance process (an OS process with env-frozen data dirs and no identity
// column anywhere), so migration maps instance slug -> user_id 1:1.
// ---------------------------------------------------------------------------
table "worlds" {
  schema = schema.main

  column "id" {
    null    = false
    type    = text
    comment = "opaque stable id; never derived from position or ordering"
  }
  column "slug" {
    null = false
    type = text
  }
  column "user_id" {
    null = false
    type = text
  }
  column "display_name" {
    null = false
    type = text
  }
  column "status" {
    null    = false
    type    = text
    default = "active"
  }
  column "bundle_dir" {
    null    = true
    type    = text
    comment = "absolute path to the sealed dac.world-bundle/v1, when mounted"
  }
  column "created_at" {
    null = false
    type = text
  }
  column "updated_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.id]
  }

  // The resolver's only lookup keys: id, or (user_id, slug).
  index "idx_worlds_user_slug" {
    unique  = true
    columns = [column.user_id, column.slug]
  }

  index "idx_worlds_user_status" {
    columns = [column.user_id, column.status]
  }

  check "worlds_status_enum" {
    expr = "status IN ('active', 'archived', 'draft')"
  }

  check "worlds_slug_nonempty" {
    expr = "length(slug) > 0"
  }

  check "worlds_user_nonempty" {
    expr = "length(user_id) > 0"
  }
}

// ---------------------------------------------------------------------------
// entities — everything a world knows about: terms, categories, agents,
// goals, experiments, documents.
// ---------------------------------------------------------------------------
table "entities" {
  schema = schema.main

  column "id" {
    null = false
    type = text
  }
  column "world_id" {
    null = false
    type = text
  }
  column "kind" {
    null    = false
    type    = text
    comment = "term | category | agent | goal | experiment | document | note"
  }
  column "name" {
    null = false
    type = text
  }
  column "title" {
    null = true
    type = text
  }
  column "body" {
    null    = true
    type    = text
    comment = "short authored text; large content lives in LanceDB or on disk"
  }
  column "attrs_json" {
    null    = true
    type    = text
    comment = "kind-specific attributes; opaque to the schema"
  }
  column "created_at" {
    null = false
    type = text
  }
  column "updated_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.id]
  }

  foreign_key "fk_entities_world" {
    columns     = [column.world_id]
    ref_columns = [table.worlds.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  index "idx_entities_world_kind_name" {
    unique  = true
    columns = [column.world_id, column.kind, column.name]
  }

  index "idx_entities_world_kind" {
    columns = [column.world_id, column.kind]
  }

  check "entities_kind_enum" {
    expr = "kind IN ('term', 'category', 'agent', 'goal', 'experiment', 'document', 'note')"
  }

  check "entities_name_nonempty" {
    expr = "length(name) > 0"
  }
}

// ---------------------------------------------------------------------------
// relations — adjacency table. Graph traversal via recursive CTEs.
// ---------------------------------------------------------------------------
table "relations" {
  schema = schema.main

  column "id" {
    null = false
    type = text
  }
  column "world_id" {
    null = false
    type = text
  }
  column "src_entity_id" {
    null = false
    type = text
  }
  column "dst_entity_id" {
    null = false
    type = text
  }
  column "kind" {
    null    = false
    type    = text
    comment = "relates_to | parent_of | derived_from | cites | contradicts"
  }
  column "weight" {
    null    = true
    type    = real
    comment = "association strength; NULL means unweighted"
  }
  column "created_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.id]
  }

  foreign_key "fk_relations_world" {
    columns     = [column.world_id]
    ref_columns = [table.worlds.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  foreign_key "fk_relations_src" {
    columns     = [column.src_entity_id]
    ref_columns = [table.entities.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  foreign_key "fk_relations_dst" {
    columns     = [column.dst_entity_id]
    ref_columns = [table.entities.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  index "idx_relations_edge" {
    unique  = true
    columns = [column.src_entity_id, column.dst_entity_id, column.kind]
  }

  index "idx_relations_src" {
    columns = [column.src_entity_id, column.kind]
  }

  index "idx_relations_dst" {
    columns = [column.dst_entity_id, column.kind]
  }

  index "idx_relations_world" {
    columns = [column.world_id]
  }

  check "relations_kind_enum" {
    expr = "kind IN ('relates_to', 'parent_of', 'derived_from', 'cites', 'contradicts')"
  }

  check "relations_no_self_edge" {
    expr = "src_entity_id <> dst_entity_id"
  }
}

// ---------------------------------------------------------------------------
// world_state — mutable tick/live state, deliberately isolated from the
// immutable content tables so high-churn writes do not fragment them.
//
// This is the table that in 1.x was a scatter of JSON files (goals/,
// activity.jsonl, agent_workflows.json, world-mount.json) with no
// transaction boundary between them.
// ---------------------------------------------------------------------------
table "world_state" {
  schema = schema.main

  column "world_id" {
    null = false
    type = text
  }
  column "key" {
    null = false
    type = text
  }
  column "value_json" {
    null = false
    type = text
  }
  column "tick" {
    null    = false
    type    = integer
    default = 0
  }
  column "updated_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.world_id, column.key]
  }

  foreign_key "fk_world_state_world" {
    columns     = [column.world_id]
    ref_columns = [table.worlds.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  index "idx_world_state_tick" {
    columns = [column.world_id, column.tick]
  }
}

// ---------------------------------------------------------------------------
// content_refs — the join seam to LanceDB, and the drift detector.
//
// In 1.x nothing recorded how a vector was produced: every table stored a
// 128-dim SHA1 token-hash projection with no provenance column, and world
// scoping was achieved by `rm -rf` of the other worlds' staged files. This
// table replaces both. `world_id` makes vector queries filterable, and
// (embedding_model, embedding_dim) let `doctor` prove that every row was
// embedded by the model the current spec declares.
// ---------------------------------------------------------------------------
table "content_refs" {
  schema = schema.main

  column "id" {
    null = false
    type = text
  }
  column "world_id" {
    null = false
    type = text
  }
  column "entity_id" {
    null    = true
    type    = text
    comment = "the entity this content backs, when there is one"
  }
  column "lance_table" {
    null = false
    type = text
  }
  column "lance_uri" {
    null    = false
    type    = text
    comment = "dataset URI; the row is addressed by (lance_table, row_key)"
  }
  column "row_key" {
    null    = false
    type    = text
    comment = "primary key of the Lance row, e.g. the PKB relative path"
  }
  column "source_path" {
    null    = true
    type    = text
    comment = "filesystem origin, for large artifacts referenced by path"
  }
  column "content_sha256" {
    null = true
    type = text
  }
  column "embedding_model" {
    null = false
    type = text
  }
  column "embedding_dim" {
    null = false
    type = integer
  }
  column "ingested_at" {
    null = false
    type = text
  }

  primary_key {
    columns = [column.id]
  }

  foreign_key "fk_content_refs_world" {
    columns     = [column.world_id]
    ref_columns = [table.worlds.column.id]
    on_delete   = CASCADE
    on_update   = CASCADE
  }

  foreign_key "fk_content_refs_entity" {
    columns     = [column.entity_id]
    ref_columns = [table.entities.column.id]
    on_delete   = SET_NULL
    on_update   = CASCADE
  }

  // Row identity is (world_id, lance_table, row_key) — NOT (lance_table,
  // row_key). Row keys are only unique within a world: every world's PKB has
  // its own `agents/README.md`, and the 1.x lab on disk has 36 such paths
  // shared across its four worlds. Scoping this index globally would let one
  // world's ingest silently reassign another world's content_ref, which is
  // the same class of cross-world bleed the world_id column exists to stop.
  index "idx_content_refs_row" {
    unique  = true
    columns = [column.world_id, column.lance_table, column.row_key]
  }

  index "idx_content_refs_world" {
    columns = [column.world_id, column.lance_table]
  }

  index "idx_content_refs_embedding" {
    columns = [column.embedding_model, column.embedding_dim]
  }

  check "content_refs_dim_positive" {
    expr = "embedding_dim > 0"
  }

  check "content_refs_model_nonempty" {
    expr = "length(embedding_model) > 0"
  }
}
