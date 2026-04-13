# CodeQL Setup for deepiri-prismpipe

This folder contains the CodeQL configuration for security scanning in this service.

## What each file does

- `.github/workflows/codeql.yml`
	- Defines when scans run and how GitHub Actions executes CodeQL.
- `.github/codeql/codeql-config.yml`
	- Defines what folders and files to include and ignore during analysis.

## CodeQL workflow breakdown (`.github/workflows/codeql.yml`)

### `name: CodeQL`
The display name in the Actions tab.

### `on.pull_request.branches` and `on.push.branches`
```yaml
on:
	pull_request:
		branches: [main, dev]
	push:
		branches: [main, dev]
```
Runs scans when PRs target `main` or `dev`, and when commits are pushed to `main` or `dev`.

### `permissions`
```yaml
permissions:
	actions: read
	contents: read
	security-events: write
```
Uses least-privilege permissions. `security-events: write` is required so CodeQL can upload findings.

### Language setup (current)
```yaml
with:
	languages: python
```
This workflow currently runs analysis for Python.

### Checkout step
```yaml
with:
	fetch-depth: 0
```
- `fetch-depth: 0` keeps full git history (safe default for analysis and troubleshooting).

### Initialize CodeQL
```yaml
uses: github/codeql-action/init@v3
with:
	config-file: ./.github/codeql/codeql-config.yml
```
Starts the CodeQL engine and loads `.github/codeql/codeql-config.yml`.

### Analyze
```yaml
uses: github/codeql-action/analyze@v3
```
Executes queries and uploads results to GitHub Security.

## Config breakdown (`.github/codeql/codeql-config.yml`)

### `paths`
The current include list is intentionally scoped to maintained Python source and tests:

```yaml
paths:
	- src
	- server.py
	- tests
```

### `paths-ignore`
Generated/build/cache artifact paths are excluded to reduce noise and runtime:

```yaml
paths-ignore:
	- '**/__pycache__/**'
	- '**/.pytest_cache/**'
	- '**/.mypy_cache/**'
	- '**/.venv/**'
	- '**/venv/**'
	- '**/dist/**'
	- '**/build/**'
	- '**/*.min.js'
```

## Best practices

1. Keep trigger scope intentional.
	 Use branch filters (`main`, `dev`) to control cost and noise.
2. Keep language list explicit.
	 Only include languages with meaningful source code.
3. Keep `paths` focused when used.
	 Include actively maintained production code first.
4. Exclude generated/cache artifacts.
	 Keep build outputs, runtime caches, and minified files in `paths-ignore`.
5. Pin to stable major action versions.
	 `@v3` is the current stable major for CodeQL actions.
6. Review alerts regularly.
	 Triage high/critical findings first and suppress only with documented reasoning.

## Maintenance examples

### Keep language scope aligned with this service
This workflow currently analyzes Python only:

```yaml
with:
	languages: python
```

Only change this value when this service adds production code in another supported language.

### Include only specific top-level packages and entrypoints
Add explicit `paths` only for directories/files that exist in this checkout.

Example:

```yaml
paths:
	- src
	- tests
```

### Exclude another generated folder
Add a glob to `paths-ignore`, for example:

```yaml
- '**/generated/**'
```