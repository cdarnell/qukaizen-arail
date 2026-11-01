// ARAIL 2.0 world spec — compiled to a generated resolver.
//
// The resolver accepts an explicit id or slug ONLY. There is no positional
// lookup, no "first available", no alphabetical, no largest, no most-recent.
// On a miss it raises with the requested identifier, the reason, and the list
// of valid alternatives for that user.
//
// There is no fallback branch to generate. That is the design.
//
// What this replaces, from the Phase 1 audit:
//   scripts/start.sh:417            catalog[0] when exactly one world exists
//   src/arail/world_mount.py:779    catalog sorted alphabetically by display name
//   src/arail/world_mount.py:684    corrupt mount pointer -> silently None
//   src/arail/world_mount.py:679    missing ARAIL_DATA_DIR -> root lab's pointer
//   scripts/lib/instances.sh:429    "first live instance", glob order
//   scripts/start.sh:504            picker Enter-default -> option 0

resolver {
  accept_id                 = true
  accept_slug               = true
  allow_positional_fallback = false
  allow_first_available     = false
  allow_most_recent         = false
  allow_alphabetical        = false

  // On miss: raise, and name the requested identifier, the reason, and the
  // valid alternatives for that user. Never substitute a different world.
  on_miss                = "raise"
  report_alternatives    = true
  scope_alternatives_to_user = true

  // A slug is resolved within a user's namespace, never globally.
  // UNIQUE(user_id, slug) in the schema is what makes this well-defined.
  slug_scope = "user"
}

// ---------------------------------------------------------------------------
// World types — what kinds of entities and relations a world may hold.
//
// The 1.x build path hardcoded a photography category tuple as the default
// corpus scope for EVERY world (build/world_corpus.py:38-40), and its own
// docstring admitted this "is wrong for every other World". Declaring the
// allowed kinds per world type is how that stops being a hardcoded default.
// ---------------------------------------------------------------------------
world_type "knowledge" {
  description   = "A curated domain knowledge base: terms, categories, an association graph"
  entity_kinds  = ["term", "category", "document", "note"]
  relation_kinds = ["relates_to", "parent_of", "cites"]
  default       = true
}

world_type "research" {
  description   = "A knowledge world that also runs experiments and autoresearch goals"
  entity_kinds  = ["term", "category", "document", "note", "goal", "experiment", "agent"]
  relation_kinds = ["relates_to", "parent_of", "cites", "derived_from", "contradicts"]
  default       = false
}

// ---------------------------------------------------------------------------
// Status lifecycle. Mirrors the CHECK constraint in spec/schema/schema.hcl —
// the compiler verifies the two agree, so they cannot drift.
// ---------------------------------------------------------------------------
status "draft" {
  resolvable  = true
  description = "Being forged; resolvable by explicit slug so the operator can work on it"
}

status "active" {
  resolvable  = true
  description = "Normal operation"
}

status "archived" {
  resolvable  = true
  selectable  = false
  description = "Resolvable by explicit id or slug, but never offered as a choice. Resolving one is not an error — silently substituting a different world would be."
}
