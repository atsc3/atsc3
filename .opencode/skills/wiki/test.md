# Wiki Skill Tests

## Test Ingest

```
/wiki ingest my-article.md
```

Expected:
- Source summary created in `wiki/sources/`
- Entity/concept pages created as needed
- `index.md` updated
- `log.md` appended

## Test Query

```
/wiki query What is the LLM Wiki pattern?
```

Expected:
- Search index for relevant pages
- Synthesize answer with citations
- Offer to save as analysis page

## Test Lint

```
/wiki lint
```

Expected:
- Scan for contradictions
- Check for orphans
- Report actionable items

## Test Natural Language

```
"add this document to the wiki"
```

Expected: Triggers ingest operation

```
"tell me about RAG"
```

Expected: Triggers query operation

```
"check if the wiki is healthy"
```

Expected: Triggers lint operation
