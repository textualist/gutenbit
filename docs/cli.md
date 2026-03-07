# CLI

## Search catalog

```bash
gutenbit catalog --author "Austen"
```

## Ingest one book

```bash
gutenbit ingest 1342
```

## Search stored text

```bash
gutenbit search "truth universally acknowledged"
```

## View structure and content

```bash
gutenbit toc 1342
gutenbit view 1342 --section 1 -n 3
```

## JSON output

```bash
gutenbit search "whale" --json
```
