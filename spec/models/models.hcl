// ARAIL 2.0 model registry — compiled to a generated Python registry.
//
// The generated registry is the ONLY resolution path. No call site resolves
// a model any other way.
//
// HARD CEILING: no model at or above 8B parameters may serve as the
// answering model. No escalation, no override flag, no fallback exception.
// The ceiling is enforced at COMPILE time — a spec that violates it fails to
// build. That is the whole point of putting it here rather than in a runtime
// check that a caller can forget or bypass.
//
// Parameter counts are sourced from the GGUF header or the published config
// metadata. Filenames are NEVER trusted: "7b" in a filename is marketing, not
// a measurement. Every model declares `parameter_source` naming where its
// count came from, and `./arailctl db doctor` re-verifies declared counts
// against the GGUF header for any model actually present on disk.
//
// If a parameter count cannot be determined with confidence, declare
// `parameter_count = -1`. The model is then ineligible for every role and the
// build says so by name.

ceiling "answering" {
  role           = "answering"
  max_parameters = 8000000000
  bound          = "exclusive"
  rationale      = "8B+ answering models do not fit the local-first promise on a 16GB machine, and a cloud escape hatch would silently break airgapped mode."
}

// ---------------------------------------------------------------------------
// Embedding — global, and global on purpose.
//
// Schema versioning is GLOBAL: all worlds share one embedding model and one
// vector dimension at any given spec version. Per-world or per-user variation
// is corruption by definition, which is what makes the doctor's drift check
// unambiguous.
//
// 1.x had no embedding model at all: every vector was a 128-dim SHA1
// token-hash projection (a hashed bag of words), so retrieval was lexical
// overlap dressed up as semantic search. nomic-embed-text is served through
// Ollama, which is already a hard dependency, so this adds no new runtime
// dependency and works airgapped after the first pull.
// ---------------------------------------------------------------------------
model "nomic-embed-text" {
  role             = "embedding"
  backend          = "ollama"
  ollama_tag       = "nomic-embed-text"
  base             = "nomic-ai/nomic-embed-text-v1.5"
  parameter_count  = 136731648
  parameter_source = "hf_config"
  embedding_dim    = 768
  license          = "apache-2.0"
  tier             = "minimalist"
  default          = true

  // nomic-embed-text is trained with task prefixes and expects them at
  // inference. Measured on this corpus, prefixing widens the margin between
  // a relevant and an irrelevant document by ~15%. Asymmetric on purpose:
  // queries and documents get different prefixes.
  query_prefix    = "search_query: "
  document_prefix = "search_document: "
}

// ---------------------------------------------------------------------------
// Answering models.
//
// llama-ai-eng carries a REQUIRED disclosure under the Llama 3.2 Community
// License: the name must begin with "Llama", "Built with Llama" must be
// displayed, and NOTICE must bundle the license and AUP. Do not hide this
// base. (The hide-the-base rule applies only to the Apache-2.0 Qwen lineage.)
// ---------------------------------------------------------------------------
model "llama-ai-eng" {
  role             = "answering"
  backend          = "ollama"
  ollama_tag       = "llama-ai-eng"
  base             = "meta-llama/Llama-3.2-1B-Instruct"
  parameter_count  = 1235814432
  parameter_source = "hf_config"
  license          = "llama-3.2-community"
  disclosure       = "Built with Llama"
  tier             = "minimalist"
  default          = true
}

model "ai-engineer" {
  role             = "answering"
  backend          = "ollama"
  ollama_tag       = "ai-engineer"
  base             = "Qwen/Qwen2.5-7B-Instruct"
  parameter_count  = 7615616512
  parameter_source = "hf_config"
  license          = "apache-2.0"
  tier             = "maximus"
  default          = false
}
