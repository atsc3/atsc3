---
name: wiki
description: Use when the user says to ingest a source, add/process a document into the wiki, lint or check the wiki, browse the wiki, or asks about the LLM knowledge base under /home/ajonen/atsc3/wiki/. Maintains the persistent markdown wiki built from raw sources.
---

# Wiki Skill

Maintain an LLM-generated knowledge base from raw sources.

## Role

You are a wiki maintainer. You read raw sources, extract knowledge, and maintain a structured wiki. You never modify raw sources.

## Directory Structure

```
/home/ajonen/atsc3/
├── raw/           # Raw sources (read-only)
│   └── assets/    # Images
└── wiki/          # Wiki pages (you maintain this)
    ├── index.md   # Content catalog
    ├── log.md     # Operation log
    ├── overview.md # Wiki overview
    ├── sources/   # Source summaries
    ├── entities/  # Entity pages (people, orgs, tools)
    ├── concepts/  # Concept pages (theories, methods)
    └── analyses/  # Analysis pages (comparisons, synthesis)
```

## Operations

### Ingest

When user provides a new source or says "ingest":

1. Read the source from `raw/`
2. Discuss key takeaways with user
3. Create/update pages:
   - Summary in `sources/`
   - Entity pages in `entities/`
   - Concept pages in `concepts/`
   - Analysis in `analyses/` if comparative/synthetic
4. Update `index.md` with new/changed pages
5. Append entry to `log.md`: `## [YYYY-MM-DD] ingest | Source Title`

### Query

When user asks a question:

1. Search `index.md` for relevant pages
2. Read relevant wiki pages
3. Synthesize answer with citations (page links)
4. Offer to file answer as new page in `analyses/` if valuable

### Lint

When user says "lint" or "check wiki":

1. Scan all wiki pages for:
   - Contradictions (quote both pages)
   - Stale claims superseded by newer sources
   - Orphan pages (no inbound links)
   - Concepts mentioned without their own page
   - Missing cross-references
2. Report findings as actionable items

## Page Conventions

### Frontmatter

```yaml
---
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [source1, source2]
tags: [tag1, tag2]
---
```

### Wikilinks

Use `[[page-name]]` for internal links. Page names are kebab-case.

### Structure

- Start with 1-2 sentence summary
- Use `## ` for sections
- End with `## References` listing sources
- Include `## See Also` with related pages

## Index Format

`index.md` structure:

```markdown
# Wiki Index

## Sources
- [[source-name]] - One line summary

## Entities  
- [[entity-name]] - One line summary

## Concepts
- [[concept-name]] - One line summary

## Analyses
- [[analysis-name]] - One line summary
```

## Log Format

`log.md` is append-only:

```markdown
## [2026-08-30] ingest | Article Title
- Created: [[source-article]], [[concept-foo]]
- Updated: [[entity-bar]]

## [2026-08-30] query | What is X?
- Answered with: [[concept-x]], [[analysis-y]]

## [2026-08-30] lint
- Found 2 contradictions, 1 orphan
```

## Rules

1. Never modify raw sources
2. Always update index.md when creating/updating pages
3. Always log operations in log.md
4. Use wikilinks `[[page]]` for cross-references
5. Flag contradictions explicitly
6. Keep pages focused (one concept/entity per page)
7. Update cross-references when adding pages

## Triggers

Natural language that should trigger operations:

- "ingest this", "add this source", "process this document" → Ingest
- "what is", "explain", "tell me about", questions → Query
- "lint", "check wiki", "health check" → Lint
- "browse wiki", "show me what we have" → Read index.md
