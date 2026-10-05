# LLM Wiki

Personal knowledge base using the LLM Wiki pattern from [Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Structure

- `raw/` - Raw source documents (immutable, LLM reads only)
  - `assets/` - Images and attachments
- `wiki/` - LLM-generated markdown files (summaries, entities, concepts, analyses)

## Quick Start

1. Add source documents to `raw/`
2. Ask the LLM to ingest: "ingest this document" or "/wiki ingest"
3. Query the wiki: "what is X?" or "/wiki query what is X?"
4. Browse with Obsidian for graph view and backlinks

## Commands

- **Ingest**: Process new sources from `raw/` into the wiki
- **Query**: Ask questions against the wiki
- **Lint**: Health-check the wiki for contradictions, stale claims, orphans

## Architecture

Three layers:

1. **Raw sources** - Your curated collection (articles, papers, notes)
2. **The wiki** - LLM-maintained structured knowledge
3. **The schema** - This README + conventions that tell the LLM how to maintain the wiki

See `wiki/` for generated knowledge pages.
