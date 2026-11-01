-- Create "schema_version" table
CREATE TABLE `schema_version` (
  `version` integer NOT NULL,
  `spec_sha256` text NOT NULL,
  `applied_at` text NOT NULL,
  PRIMARY KEY (`version`)
);
-- Create "worlds" table
CREATE TABLE `worlds` (
  `id` text NOT NULL,
  `slug` text NOT NULL,
  `user_id` text NOT NULL,
  `display_name` text NOT NULL,
  `status` text NOT NULL DEFAULT 'active',
  `bundle_dir` text NULL,
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `worlds_status_enum` CHECK (status IN ('active', 'archived', 'draft')),
  CONSTRAINT `worlds_slug_nonempty` CHECK (length(slug) > 0),
  CONSTRAINT `worlds_user_nonempty` CHECK (length(user_id) > 0)
);
-- Create index "idx_worlds_user_slug" to table: "worlds"
CREATE UNIQUE INDEX `idx_worlds_user_slug` ON `worlds` (`user_id`, `slug`);
-- Create index "idx_worlds_user_status" to table: "worlds"
CREATE INDEX `idx_worlds_user_status` ON `worlds` (`user_id`, `status`);
-- Create "entities" table
CREATE TABLE `entities` (
  `id` text NOT NULL,
  `world_id` text NOT NULL,
  `kind` text NOT NULL,
  `name` text NOT NULL,
  `title` text NULL,
  `body` text NULL,
  `attrs_json` text NULL,
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_entities_world` FOREIGN KEY (`world_id`) REFERENCES `worlds` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `entities_kind_enum` CHECK (kind IN ('term', 'category', 'agent', 'goal', 'experiment', 'document', 'note')),
  CONSTRAINT `entities_name_nonempty` CHECK (length(name) > 0)
);
-- Create index "idx_entities_world_kind_name" to table: "entities"
CREATE UNIQUE INDEX `idx_entities_world_kind_name` ON `entities` (`world_id`, `kind`, `name`);
-- Create index "idx_entities_world_kind" to table: "entities"
CREATE INDEX `idx_entities_world_kind` ON `entities` (`world_id`, `kind`);
-- Create "relations" table
CREATE TABLE `relations` (
  `id` text NOT NULL,
  `world_id` text NOT NULL,
  `src_entity_id` text NOT NULL,
  `dst_entity_id` text NOT NULL,
  `kind` text NOT NULL,
  `weight` real NULL,
  `created_at` text NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_relations_world` FOREIGN KEY (`world_id`) REFERENCES `worlds` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_relations_src` FOREIGN KEY (`src_entity_id`) REFERENCES `entities` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_relations_dst` FOREIGN KEY (`dst_entity_id`) REFERENCES `entities` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `relations_kind_enum` CHECK (kind IN ('relates_to', 'parent_of', 'derived_from', 'cites', 'contradicts')),
  CONSTRAINT `relations_no_self_edge` CHECK (src_entity_id <> dst_entity_id)
);
-- Create index "idx_relations_edge" to table: "relations"
CREATE UNIQUE INDEX `idx_relations_edge` ON `relations` (`src_entity_id`, `dst_entity_id`, `kind`);
-- Create index "idx_relations_src" to table: "relations"
CREATE INDEX `idx_relations_src` ON `relations` (`src_entity_id`, `kind`);
-- Create index "idx_relations_dst" to table: "relations"
CREATE INDEX `idx_relations_dst` ON `relations` (`dst_entity_id`, `kind`);
-- Create index "idx_relations_world" to table: "relations"
CREATE INDEX `idx_relations_world` ON `relations` (`world_id`);
-- Create "world_state" table
CREATE TABLE `world_state` (
  `world_id` text NOT NULL,
  `key` text NOT NULL,
  `value_json` text NOT NULL,
  `tick` integer NOT NULL DEFAULT 0,
  `updated_at` text NOT NULL,
  PRIMARY KEY (`world_id`, `key`),
  CONSTRAINT `fk_world_state_world` FOREIGN KEY (`world_id`) REFERENCES `worlds` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
);
-- Create index "idx_world_state_tick" to table: "world_state"
CREATE INDEX `idx_world_state_tick` ON `world_state` (`world_id`, `tick`);
-- Create "content_refs" table
CREATE TABLE `content_refs` (
  `id` text NOT NULL,
  `world_id` text NOT NULL,
  `entity_id` text NULL,
  `lance_table` text NOT NULL,
  `lance_uri` text NOT NULL,
  `row_key` text NOT NULL,
  `source_path` text NULL,
  `content_sha256` text NULL,
  `embedding_model` text NOT NULL,
  `embedding_dim` integer NOT NULL,
  `ingested_at` text NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_content_refs_world` FOREIGN KEY (`world_id`) REFERENCES `worlds` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_content_refs_entity` FOREIGN KEY (`entity_id`) REFERENCES `entities` (`id`) ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT `content_refs_dim_positive` CHECK (embedding_dim > 0),
  CONSTRAINT `content_refs_model_nonempty` CHECK (length(embedding_model) > 0)
);
-- Create index "idx_content_refs_row" to table: "content_refs"
CREATE UNIQUE INDEX `idx_content_refs_row` ON `content_refs` (`world_id`, `lance_table`, `row_key`);
-- Create index "idx_content_refs_world" to table: "content_refs"
CREATE INDEX `idx_content_refs_world` ON `content_refs` (`world_id`, `lance_table`);
-- Create index "idx_content_refs_embedding" to table: "content_refs"
CREATE INDEX `idx_content_refs_embedding` ON `content_refs` (`embedding_model`, `embedding_dim`);
