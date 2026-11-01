// ARAIL 2.0 vector spec — compiled to the LanceDB reconciler's desired state.
//
// `./arailctl db reconcile` reads this declared state, inspects the actual
// Lance tables, and diffs. It auto-applies safe changes (add / rename /
// retype / drop column — Lance supports these without rewriting the dataset)
// and REFUSES destructive changes without --allow-destructive (dimension
// change, distance-metric change, index-type change all require a rebuild
// and must never happen silently).
//
// Dry-run by default. `--apply` to execute.

defaults {
  embedding_model   = "nomic-embed-text"
  embedding_dim     = 768
  distance          = "cosine"
  max_fragments     = 100
  version_retention = 20

  // Below this row count an ANN index costs more than the flat scan it
  // replaces, and IVF_PQ training is not meaningful. The reconciler leaves
  // small tables unindexed and says so, rather than building a degenerate
  // index. Every 1.x table was under this threshold, which is why the audit
  // found zero indexes and no recall problem to fix.
  index_min_rows = 256
}

// ---------------------------------------------------------------------------
// pkb_pages — the Personal Knowledge Base index.
//
// The 1.x schema was {path, name, vector, mtime, source_kind} with NO world
// column, so nothing could scope a query to a world. Scoping was achieved by
// physically deleting the other worlds' staged files (`_sweep_other_worlds`
// rm -rf), which is why unmounting left a world's rows searchable and why
// generated repo docs leaked farming vocabulary into every world's results.
//
// `world_id` fixes that at the storage layer: queries filter, they do not
// delete. The column is the join key to content_refs.
// ---------------------------------------------------------------------------
table "pkb_pages" {
  root        = "pkb"
  subpath     = ".cache/lancedb"
  description = "PKB page index — one row per indexed knowledge-base file"

  column "path"        { type = "string", nullable = false, primary = true }
  column "name"        { type = "string", nullable = false }
  column "world_id"    { type = "string", nullable = false }
  column "mtime"       { type = "double", nullable = false }
  column "source_kind" { type = "string", nullable = false }

  vector "vector" {
    dim    = 768
    metric = "cosine"
  }

  index "pkb_pages_vector_idx" {
    column          = "vector"
    type            = "IVF_PQ"
    metric          = "cosine"
    num_partitions  = 16
    num_sub_vectors = 24
  }

  index "pkb_pages_world_idx" {
    column = "world_id"
    type   = "BTREE"
  }
}

// ---------------------------------------------------------------------------
// wiki_nodes — the generated wiki / knowledge-graph node index.
// ---------------------------------------------------------------------------
table "wiki_nodes" {
  root        = "pkb"
  subpath     = ".wiki-cache/lancedb"
  description = "Wiki node index — one row per wiki section"

  column "slug"     { type = "string", nullable = false, primary = true }
  column "section"  { type = "string", nullable = false }
  column "title"    { type = "string", nullable = false }
  column "world_id" { type = "string", nullable = false }

  vector "vector" {
    dim    = 768
    metric = "cosine"
  }

  index "wiki_nodes_vector_idx" {
    column          = "vector"
    type            = "IVF_PQ"
    metric          = "cosine"
    num_partitions  = 16
    num_sub_vectors = 24
  }

  index "wiki_nodes_world_idx" {
    column = "world_id"
    type   = "BTREE"
  }
}

// ---------------------------------------------------------------------------
// agent_workflows — live agent state.
//
// This table holds 2-19 rows but accumulated 276 versions in one instance and
// 150 in another, because nothing ever compacted it: 1.71 MB on disk for
// 0.002 MB of live data. A tight retention window plus `db optimize` is the
// fix; the churn itself is expected and fine.
// ---------------------------------------------------------------------------
table "agent_workflows" {
  root        = "data"
  subpath     = "lance"
  description = "Agent workflow state — one row per agent"

  column "agent_id"     { type = "string", nullable = false, primary = true }
  column "world_id"     { type = "string", nullable = false }
  column "status"       { type = "string", nullable = false }
  column "objective"    { type = "string", nullable = true }
  column "current_task" { type = "string", nullable = true }
  column "next_step"    { type = "string", nullable = true }
  column "pause_reason" { type = "string", nullable = true }
  column "updated_at"   { type = "string", nullable = false }
  column "summary"      { type = "string", nullable = true }

  vector "vector" {
    dim    = 768
    metric = "cosine"
  }

  // High-churn table: retain far fewer versions than the default.
  version_retention = 5
  max_fragments     = 20
}

// ---------------------------------------------------------------------------
// experiments — the on-device experiment engine's index.
// ---------------------------------------------------------------------------
table "experiments" {
  root        = "data"
  subpath     = "experiments/.cache/lancedb"
  description = "Experiment index — one row per experiment"

  column "id"       { type = "string", nullable = false, primary = true }
  column "world_id" { type = "string", nullable = false }
  column "domain"   { type = "string", nullable = false }
  column "status"   { type = "string", nullable = false }

  vector "vector" {
    dim    = 768
    metric = "cosine"
  }

  version_retention = 10
  max_fragments     = 20
}
